"""Code-based scoring for one agent run, and aggregation across runs.

Everything here is pure (no model calls), so it is unit tested offline.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.ai.tools import TOOL_SCOPES
from evals.dataset import Case, ExpectedCall, fill_dates

# Arguments the model may omit because the tool has a default (e.g. country_code
# "KE"), so matchers see what the tool actually ran with.
TOOL_DEFAULTS = {
    t.name: {arg: spec["default"] for arg, spec in t.args.items() if "default" in spec} for t, _ in TOOL_SCOPES
}

# Deliberately broad: a must-decline case fails here only if the answer has no
# refusal language at all. The LLM judge checks the decline is real.
_DECLINE = re.compile(
    r"\b(?:can(?:not|'t)|can only|unable|not able|sorry|apologi[sz]e|only (?:help|answer|assist|provide|handle)"
    r"|(?:don't|do not) have (?:access|the ability|permission|a tool|any tool)|no access|not (?:permitted|authori[sz]ed|allowed)"
    r"|outside (?:my|the) scope|beyond (?:my|the) scope|weather[- ]related)\b"
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class RunResult:
    """One run of one case; `status` is pass, fail or error (provider/run failure)."""

    case_id: str
    category: str
    repeat: int
    status: str = "error"
    question: str = ""
    answer: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    judge: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    judge_usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    latency_ms: int | None = None
    attempts: int = 0
    error: dict[str, str] | None = None

    def finalize(self) -> "RunResult":
        if self.error is None:
            self.status = "pass" if all(c.passed for c in self.checks) else "fail"
        return self


def match_arg(value: Any, matcher: dict[str, Any]) -> bool:
    """True if `value` satisfies every op in `matcher` (strings compare case-insensitively except `equals`)."""
    text = str(value).casefold() if value is not None else ""
    for op, expected in matcher.items():
        match op:
            case "equals":
                ok = value == expected
            case "iequals":
                ok = value is not None and text == str(expected).casefold()
            case "icontains":
                ok = value is not None and str(expected).casefold() in text
            case "one_of":
                ok = any(
                    text == str(e).casefold() if isinstance(e, str) else value == e for e in expected
                )
            case "gte" | "lte":
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return False
                ok = number >= expected if op == "gte" else number <= expected
            case _:
                raise ValueError(f"unknown matcher op {op!r}")
        if not ok:
            return False
    return True


def effective_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {**TOOL_DEFAULTS.get(name, {}), **args}


def call_matches(expected: ExpectedCall, actual: dict[str, Any]) -> bool:
    if actual["name"] != expected.name:
        return False
    args = effective_args(actual["name"], actual["args"])
    return all(match_arg(args.get(arg), matcher) for arg, matcher in expected.args.items())


def unmatched_calls(expected: list[ExpectedCall], actual: list[dict[str, Any]], by_name_only: bool = False) -> list[ExpectedCall]:
    """Expected calls left over after pairing each with a distinct actual call.

    Backtracking finds a full pairing if one exists; lists are a handful long.
    """
    def fits(e: ExpectedCall, a: dict[str, Any]) -> bool:
        return a["name"] == e.name if by_name_only else call_matches(e, a)

    def solve(i: int, used: frozenset[int]) -> bool:
        if i == len(expected):
            return True
        return any(j not in used and fits(expected[i], a) and solve(i + 1, used | {j}) for j, a in enumerate(actual))

    if solve(0, frozenset()):
        return []
    # No full pairing: report the calls that cannot be matched on their own.
    return [e for e in expected if not any(fits(e, a) for a in actual)] or expected


def looks_like_decline(answer: str) -> bool:
    return bool(_DECLINE.search(answer.casefold().replace("’", "'")))


def _contains(answer: str, needle: str) -> bool:
    return needle.casefold() in answer.casefold()


def score_run(case: Case, answer: str, tool_calls: list[dict[str, Any]], canaries: list[str], today: date) -> list[Check]:
    """Deterministic checks for one run; only checks the case asks for are returned."""
    expect = case.expect
    checks = []

    names = [c["name"] for c in tool_calls]
    problems = []
    if missing := unmatched_calls(expect.tool_calls, tool_calls, by_name_only=True):
        problems.append(f"missing {[e.name for e in missing]}")
    if forbidden := sorted(set(names) & set(expect.forbidden_tools)):
        problems.append(f"called forbidden {forbidden}")
    if expect.max_tool_calls is not None and len(names) > expect.max_tool_calls:
        problems.append(f"{len(names)} calls > max {expect.max_tool_calls}")
    checks.append(Check("tool_selection", not problems, "; ".join(problems) or f"called {names}"))

    if expect.tool_calls:
        bad = unmatched_calls(expect.tool_calls, tool_calls)
        detail = "; ".join(f"no {e.name} call with {e.args}" for e in bad) if bad else "ok"
        checks.append(Check("tool_args", not bad, detail))

    if expect.must_decline:
        checks.append(Check("declined", looks_like_decline(answer), "" if looks_like_decline(answer) else "no refusal language"))

    if expect.answer_contains:
        missing_facts = []
        for item in fill_dates(expect.answer_contains, today):
            options = item if isinstance(item, list) else [item]
            if not any(_contains(answer, option) for option in options):
                missing_facts.append(options[0] if len(options) == 1 else f"one of {options}")
        checks.append(Check("answer_facts", not missing_facts, f"missing {missing_facts}" if missing_facts else "ok"))

    if expect.answer_not_contains:
        present = [s for s in fill_dates(expect.answer_not_contains, today) if _contains(answer, s)]
        checks.append(Check("answer_forbidden", not present, f"contains {present}" if present else "ok"))

    if canaries:
        leaked = [s for s in canaries if _contains(answer, s)]
        checks.append(Check("no_leak", not leaked, f"leaked {leaked}" if leaked else "ok"))

    return checks


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 4) if total else None


def aggregate(runs: list[RunResult]) -> dict[str, Any]:
    """Summary metrics. Errored runs are counted but excluded from pass rates."""
    scored = [r for r in runs if r.status != "error"]
    passed = [r for r in scored if r.status == "pass"]

    by_category: dict[str, dict[str, Any]] = {}
    for category in dict.fromkeys(r.category for r in runs):
        in_cat = [r for r in runs if r.category == category]
        cat_scored = [r for r in in_cat if r.status != "error"]
        cat_passed = sum(r.status == "pass" for r in cat_scored)
        by_category[category] = {
            "runs": len(in_cat),
            "passed": cat_passed,
            "errors": len(in_cat) - len(cat_scored),
            "pass_rate": _rate(cat_passed, len(cat_scored)),
        }

    check_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for run in scored:
        for check in run.checks:
            check_counts[check.name][0] += check.passed
            check_counts[check.name][1] += 1
    checks = {
        name: {"passed": ok, "evaluated": total, "rate": _rate(ok, total)} for name, (ok, total) in check_counts.items()
    }

    judged = [r for r in scored if r.judge and "verdict" in r.judge]
    latencies = [r.latency_ms for r in scored if r.latency_ms is not None]
    by_case: dict[str, set[str]] = defaultdict(set)
    for run in scored:
        by_case[run.case_id].add(run.status)

    return {
        "runs": len(runs),
        "scored": len(scored),
        "passed": len(passed),
        "failed": len(scored) - len(passed),
        "errors": len(runs) - len(scored),
        "pass_rate": _rate(len(passed), len(scored)) or 0.0,
        "tool_selection_accuracy": checks.get("tool_selection", {}).get("rate"),
        "by_category": by_category,
        "checks": checks,
        "judge": {
            "judged": len(judged),
            "passed": sum(r.judge["verdict"] == "pass" for r in judged),
            "errors": sum(1 for r in scored if r.judge and "error" in r.judge),
        },
        "tokens": {
            "mean_input": round(sum(r.usage["input_tokens"] for r in scored) / len(scored), 1) if scored else None,
            "mean_output": round(sum(r.usage["output_tokens"] for r in scored) / len(scored), 1) if scored else None,
            "total_input": sum(r.usage["input_tokens"] for r in runs),
            "total_output": sum(r.usage["output_tokens"] for r in runs),
            "judge_total_input": sum(r.judge_usage["input_tokens"] for r in runs),
            "judge_total_output": sum(r.judge_usage["output_tokens"] for r in runs),
        },
        "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)},
        "errors_by_kind": dict(Counter(r.error["kind"] for r in runs if r.error)),
        # With --repeat, cases that both passed and failed.
        "flaky_cases": sorted(case_id for case_id, statuses in by_case.items() if len(statuses) > 1),
    }
