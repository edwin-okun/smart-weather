"""Eval dataset: cases (JSONL, one per line) plus the fixtures they reference.

The schema is validated with pydantic on load, so a malformed case fails fast
instead of scoring as a silent pass or fail.
"""

import hashlib
import json
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai.tools import TOOL_SCOPES
from app.permissions import ALL_SCOPES

DATA_DIR = Path(__file__).parent / "data"
CASES_PATH = DATA_DIR / "cases.jsonl"
FIXTURES_PATH = DATA_DIR / "fixtures.json"

TOOL_NAMES = frozenset(t.name for t, _ in TOOL_SCOPES)
CATEGORIES = (
    "current_weather",
    "non_default_country",
    "comparison",
    "location_resolution",
    "history",
    "scope_denied",
    "off_topic",
    "prompt_injection",
    "tool_output_injection",
    "tool_failure",
    "units_dates",
)
MATCHER_OPS = frozenset({"equals", "iequals", "icontains", "one_of", "gte", "lte"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedCall(_Strict):
    name: str
    # arg name -> {op: value}; see evals.scoring.match_arg for the ops.
    args: dict[str, dict[str, Any]] = {}

    @field_validator("name")
    @classmethod
    def _known_tool(cls, name: str) -> str:
        if name not in TOOL_NAMES:
            raise ValueError(f"unknown tool {name!r}")
        return name

    @field_validator("args")
    @classmethod
    def _known_ops(cls, args: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        for arg, matcher in args.items():
            if not matcher or not set(matcher) <= MATCHER_OPS:
                raise ValueError(f"arg {arg!r}: matcher ops must be a non-empty subset of {sorted(MATCHER_OPS)}")
        return args


class Expect(_Strict):
    # Each expected call must be matched by a distinct actual call, in any order.
    tool_calls: list[ExpectedCall] = []
    forbidden_tools: list[str] = []
    max_tool_calls: int | None = Field(default=None, ge=0)
    must_decline: bool = False
    # Case-insensitive; a nested list means "any one of these".
    answer_contains: list[str | list[str]] = []
    answer_not_contains: list[str] = []

    @field_validator("forbidden_tools")
    @classmethod
    def _known_tools(cls, names: list[str]) -> list[str]:
        if unknown := set(names) - TOOL_NAMES:
            raise ValueError(f"unknown tools {sorted(unknown)}")
        return names


class Case(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    category: Literal[CATEGORIES]
    question: str = Field(min_length=1, max_length=2000)
    scopes: list[str]
    # Location fixture names, in the order the fake geocoder ranks them.
    fixtures: list[str] = []
    history: str | None = None
    # Strings injected via the question that must never reach the answer.
    canaries: list[str] = []
    expect: Expect
    # Extra case-specific criteria for the LLM judge.
    judge_notes: str | None = None

    @field_validator("scopes")
    @classmethod
    def _known_scopes(cls, scopes: list[str]) -> list[str]:
        if unknown := set(scopes) - ALL_SCOPES:
            raise ValueError(f"unknown scopes {sorted(unknown)}")
        return scopes


class FixtureError(_Strict):
    # not_found -> LocationNotFoundError, upstream -> UpstreamServiceError,
    # unexpected -> RuntimeError (the agent's generic tool-error path).
    type: Literal["not_found", "upstream", "unexpected"]
    message: str


class LocationFixture(_Strict):
    # Extra query strings the fake geocoder accepts besides geocoding["name"].
    aliases: list[str] = []
    geocoding: dict[str, Any]  # one Open-Meteo geocoding `results` entry
    forecast: dict[str, Any] | None = None  # an Open-Meteo forecast response
    error: FixtureError | None = None
    canaries: list[str] = []

    @model_validator(mode="after")
    def _forecast_xor_error(self) -> "LocationFixture":
        if (self.forecast is None) == (self.error is None):
            raise ValueError("a location fixture needs exactly one of forecast or error")
        for key in ("name", "latitude", "longitude", "country_code"):
            if key not in self.geocoding:
                raise ValueError(f"geocoding is missing {key!r}")
        return self


class HistoryEntry(_Strict):
    location: str  # location fixture supplying the place and base forecast
    city: str
    country_code: str
    looked_up_at: str
    # Overrides applied to the base forecast's `current` block.
    current: dict[str, Any] = {}


class HistorySet(_Strict):
    entries: list[HistoryEntry] = []  # newest first
    error: FixtureError | None = None
    canaries: list[str] = []


class Fixtures(_Strict):
    locations: dict[str, LocationFixture]
    history: dict[str, HistorySet]


class Dataset(BaseModel):
    cases: list[Case]
    fixtures: Fixtures
    version: str

    def canaries_for(self, case: Case) -> list[str]:
        """Every injected string that must not appear in this case's answer."""
        found = list(case.canaries)
        for name in case.fixtures:
            found += self.fixtures.locations[name].canaries
        if case.history:
            history = self.fixtures.history[case.history]
            found += history.canaries
            for entry in history.entries:
                found += self.fixtures.locations[entry.location].canaries
        return list(dict.fromkeys(found))


def load_dataset(cases_path: Path = CASES_PATH, fixtures_path: Path = FIXTURES_PATH) -> Dataset:
    cases_text = cases_path.read_text(encoding="utf-8")
    fixtures_text = fixtures_path.read_text(encoding="utf-8")
    cases = []
    for lineno, line in enumerate(cases_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(Case.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{cases_path.name} line {lineno}: {exc}") from exc
    fixtures = Fixtures.model_validate(json.loads(fixtures_text))
    # A content hash is the dataset version: it changes whenever any case or
    # fixture does, with nothing to remember to bump.
    digest = hashlib.sha256((cases_text + "\0" + fixtures_text).encode()).hexdigest()[:12]
    dataset = Dataset(cases=cases, fixtures=fixtures, version=f"sha256:{digest}")
    validate_references(dataset)
    return dataset


def validate_references(dataset: Dataset) -> None:
    """Checks that span cases and fixtures, which pydantic cannot do per model."""
    problems = []
    ids = [case.id for case in dataset.cases]
    if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"duplicate case ids: {duplicates}")
    locations, history = dataset.fixtures.locations, dataset.fixtures.history
    for case in dataset.cases:
        if missing := [name for name in case.fixtures if name not in locations]:
            problems.append(f"{case.id}: unknown location fixtures {missing}")
        if case.history is not None and case.history not in history:
            problems.append(f"{case.id}: unknown history set {case.history!r}")
    for name, history_set in history.items():
        if missing := [e.location for e in history_set.entries if e.location not in locations]:
            problems.append(f"history {name}: unknown location fixtures {missing}")
        if history_set.entries and history_set.error:
            problems.append(f"history {name}: has both entries and an error")
    if problems:
        raise ValueError("invalid eval dataset:\n  " + "\n  ".join(problems))


def select_cases(cases: list[Case], patterns: str | None = None, limit: int | None = None) -> list[Case]:
    """Filter by comma-separated ids, globs or category names, then cap at `limit`."""
    if patterns:
        wanted = [p.strip() for p in patterns.split(",") if p.strip()]
        cases = [c for c in cases if any(fnmatchcase(c.id, p) or c.category == p for p in wanted)]
    return cases[:limit] if limit is not None else cases


def fill_dates(value: Any, today: date) -> Any:
    """Replace {today} style placeholders in fixture and expectation strings.

    Fixtures are dated today so the agent never sees a stale observation.
    """
    if isinstance(value, str):
        replacements = {
            "{today}": today.isoformat(),
            "{today_month_day}": f"{today:%B} {today.day}",
            "{today_day_month}": f"{today.day} {today:%B}",
        }
        for token, text in replacements.items():
            value = value.replace(token, text)
        return value
    if isinstance(value, list):
        return [fill_dates(v, today) for v in value]
    if isinstance(value, dict):
        return {k: fill_dates(v, today) for k, v in value.items()}
    return value
