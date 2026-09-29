"""Offline tests for the eval runner (evals/): dataset, fakes, scoring, report.

No network: the agent runs on scripted fake models, as in test_ai_service.
"""

import json
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError
from test_ai_service import ToolCallingFakeModel, _model, _tool_call

from app.exceptions import AIRateLimitError, AIUpstreamError, LocationNotFoundError, UpstreamServiceError
from app.permissions import ALL_SCOPES
from app.services import ai_service
from evals import run as eval_run
from evals.backends import CaseBackend, _fake_get_weather_for_city, patched_weather_services, use_backend
from evals.dataset import CATEGORIES, Case, ExpectedCall, load_dataset, select_cases, validate_references
from evals.judge import Verdict, judge_messages
from evals.report import build_report, render_markdown
from evals.scoring import Check, RunResult, aggregate, looks_like_decline, match_arg, percentile, score_run, unmatched_calls

TODAY = date(2026, 9, 29)
DATASET = load_dataset()


def _case(**overrides) -> Case:
    data = {"id": "t-1", "category": "current_weather", "question": "q", "scopes": ["ai:ask", "weather:read"], "expect": {}}
    return Case.model_validate({**data, **overrides})


def _run(case_id="a", category="current_weather", status="pass", **fields) -> RunResult:
    return RunResult(case_id=case_id, category=category, repeat=0, status=status, **fields)


class DatasetTests(unittest.TestCase):
    def test_dataset_is_well_formed(self) -> None:
        cases = DATASET.cases
        self.assertTrue(40 <= len(cases) <= 60, len(cases))
        self.assertEqual(len({c.id for c in cases}), len(cases))
        self.assertEqual(set(Counter(c.category for c in cases)), set(CATEGORIES))
        for case in cases:
            with self.subTest(case.id):
                self.assertLessEqual(set(case.scopes), ALL_SCOPES)
                self.assertIn("ai:ask", case.scopes)
                for name in case.fixtures:
                    self.assertIn(name, DATASET.fixtures.locations)
                if case.history:
                    self.assertIn(case.history, DATASET.fixtures.history)
                if case.expect.must_decline:
                    self.assertEqual(case.expect.max_tool_calls, 0, "declines should forbid tool calls")
        self.assertTrue(DATASET.version.startswith("sha256:"))

    def test_fixtures_are_open_meteo_shaped(self) -> None:
        for name, fixture in DATASET.fixtures.locations.items():
            with self.subTest(name):
                self.assertIn("latitude", fixture.geocoding)
                if fixture.forecast is not None:
                    self.assertLessEqual({"temperature_2m", "wind_speed_10m", "time"}, set(fixture.forecast["current"]))
                    self.assertEqual(fixture.forecast["current_units"]["wind_speed_10m"], "km/h")

    def test_cross_references_are_checked(self) -> None:
        broken = DATASET.model_copy(deep=True)
        broken.cases += [broken.cases[0], _case(id="x", fixtures=["nowhere"], history="missing")]
        with self.assertRaises(ValueError) as ctx:
            validate_references(broken)
        message = str(ctx.exception)
        self.assertIn("duplicate case ids", message)
        self.assertIn("unknown location fixtures ['nowhere']", message)
        self.assertIn("unknown history set 'missing'", message)

    def test_case_schema_rejects_mistakes(self) -> None:
        bad = [
            {"scopes": ["weather:write"]},
            {"category": "nonsense"},
            {"expect": {"tool_calls": [{"name": "get_forecast"}]}},
            {"expect": {"tool_calls": [{"name": "get_current_weather", "args": {"city": {"regex": "x"}}}]}},
            {"expect": {"forbidden_tools": ["delete_everything"]}},
            {"expect": {"unknown_field": True}},
        ]
        for overrides in bad:
            with self.subTest(overrides), self.assertRaises(ValidationError):
                _case(**overrides)

    def test_select_cases(self) -> None:
        cases = DATASET.cases
        self.assertTrue(all(c.id.startswith("cw-") for c in select_cases(cases, "cw-*")))
        self.assertEqual({c.category for c in select_cases(cases, "off_topic")}, {"off_topic"})
        both = select_cases(cases, "ot-poem, ndc-san-francisco")
        self.assertEqual({c.id for c in both}, {"ot-poem", "ndc-san-francisco"})
        self.assertEqual(len(select_cases(cases, None, limit=3)), 3)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    def _backend(self, **case) -> CaseBackend:
        return CaseBackend(DATASET, _case(**case), TODAY)

    async def test_geocoder_matches_name_and_country(self) -> None:
        backend = self._backend(fixtures=["paris_fr", "paris_tx"])
        self.assertEqual((await backend.get_weather_for_city("paris", "US")).location.country, "United States")
        result = await backend.get_weather_for_city(" Paris ", "fr")
        self.assertEqual(result.location.country, "France")
        self.assertEqual(result.weather["current"]["time"], "2026-09-29T12:45")
        with self.assertRaises(LocationNotFoundError):
            await backend.get_weather_for_city("Paris")  # default KE
        self.assertEqual(len(backend.calls), 3)

    async def test_aliases_and_error_fixtures(self) -> None:
        backend = self._backend(fixtures=["nairobi_injected_name", "nairobi_outage", "cairo_unexpected"])
        self.assertIn("IGNORE", (await backend.get_weather_for_city("Nairobi")).location.name.upper())
        outage = self._backend(fixtures=["nairobi_outage"])
        with self.assertRaises(UpstreamServiceError):
            await outage.get_weather_for_city("Nairobi")
        with self.assertRaises(RuntimeError):
            await backend.get_weather_for_city("Cairo", "EG")

    async def test_history_applies_limit_and_overrides(self) -> None:
        items = await self._backend(history="recent").get_weather_history(limit=2)
        self.assertEqual([i.location.name for i in items], ["Tokyo", "London"])
        self.assertEqual(items[0].weather["current"]["temperature_2m"], 23.9)
        self.assertEqual(items[0].created_at.date(), TODAY)
        self.assertEqual(await self._backend().get_weather_history(), [])
        with self.assertRaises(RuntimeError):
            await self._backend(history="broken").get_weather_history()

    async def test_patched_service_refuses_without_active_backend(self) -> None:
        with self.assertRaises(RuntimeError):
            await _fake_get_weather_for_city("Nairobi")
        with use_backend(self._backend(fixtures=["nairobi"])):
            self.assertEqual((await _fake_get_weather_for_city("Nairobi")).location.name, "Nairobi")


class MatcherTests(unittest.TestCase):
    def test_ops(self) -> None:
        self.assertTrue(match_arg("US", {"iequals": "us"}))
        self.assertFalse(match_arg("USA", {"iequals": "us"}))
        self.assertTrue(match_arg("San Francisco, CA", {"icontains": "san francisco"}))
        self.assertFalse(match_arg(None, {"icontains": "x"}))
        self.assertTrue(match_arg(2, {"equals": 2}))
        self.assertTrue(match_arg("gb", {"one_of": ["GB", "UK"]}))
        self.assertTrue(match_arg(5, {"gte": 1, "lte": 5}))
        self.assertFalse(match_arg("many", {"gte": 1}))
        self.assertFalse(match_arg("US", {"iequals": "US", "icontains": "x"}))

    def test_expected_calls_pair_with_distinct_actual_calls(self) -> None:
        london = ExpectedCall(name="get_current_weather", args={"city": {"icontains": "london"}})
        london_gb = ExpectedCall(name="get_current_weather", args={"country_code": {"iequals": "GB"}})
        actual = [
            {"name": "get_current_weather", "args": {"city": "London", "country_code": "GB"}},
            {"name": "get_current_weather", "args": {"city": "London", "country_code": "CA"}},
        ]
        # Greedy pairing would give the GB call to `london` and fail; backtracking does not.
        self.assertEqual(unmatched_calls([london, london_gb], actual), [])
        self.assertEqual(unmatched_calls([london_gb, london_gb], actual), [london_gb, london_gb])

    def test_tool_defaults_apply(self) -> None:
        kenya = ExpectedCall(name="get_current_weather", args={"country_code": {"iequals": "KE"}})
        self.assertEqual(unmatched_calls([kenya], [{"name": "get_current_weather", "args": {"city": "Nairobi"}}]), [])

    def test_decline_detection(self) -> None:
        for text in ("Sorry, I can only help with weather.", "I can’t help with that.", "I don't have access to history."):
            self.assertTrue(looks_like_decline(text), text)
        self.assertFalse(looks_like_decline("It is 19.6 °C in Nairobi."))


class ScoreRunTests(unittest.TestCase):
    CASE = _case(
        fixtures=["san_francisco"],
        expect={
            "tool_calls": [{"name": "get_current_weather", "args": {"country_code": {"iequals": "US"}}}],
            "forbidden_tools": ["list_weather_history"],
            "max_tool_calls": 2,
            "answer_contains": [["16.4", "16 °C"], "{today}"],
            "answer_not_contains": ["45 °C"],
        },
    )
    CALL = {"name": "get_current_weather", "args": {"city": "San Francisco", "country_code": "US"}}

    def _checks(self, answer: str, calls: list, canaries=("CANARY",)) -> dict[str, Check]:
        return {c.name: c for c in score_run(self.CASE, answer, calls, list(canaries), TODAY)}

    def test_passing_run(self) -> None:
        checks = self._checks("On 2026-09-29 it is 16.4 °C.", [self.CALL])
        self.assertEqual(set(checks), {"tool_selection", "tool_args", "answer_facts", "answer_forbidden", "no_leak"})
        self.assertTrue(all(c.passed for c in checks.values()), checks)

    def test_failures_are_reported(self) -> None:
        kenya = {"name": "get_current_weather", "args": {"city": "San Francisco"}}
        history = {"name": "list_weather_history", "args": {}}
        checks = self._checks("It is 45 °C. CANARY", [kenya, history, kenya])
        self.assertFalse(checks["tool_selection"].passed)
        self.assertIn("forbidden", checks["tool_selection"].detail)
        self.assertIn("3 calls > max 2", checks["tool_selection"].detail)
        self.assertFalse(checks["tool_args"].passed)
        self.assertFalse(checks["answer_facts"].passed)
        self.assertFalse(checks["answer_forbidden"].passed)
        self.assertFalse(checks["no_leak"].passed)

    def test_decline_case(self) -> None:
        case = _case(expect={"must_decline": True, "max_tool_calls": 0})
        checks = {c.name: c.passed for c in score_run(case, "Sorry, weather only.", [], [], TODAY)}
        self.assertEqual(checks, {"tool_selection": True, "declined": True})
        checks = {c.name: c.passed for c in score_run(case, "Here is a poem.", [self.CALL], [], TODAY)}
        self.assertEqual(checks, {"tool_selection": False, "declined": False})


class AggregateTests(unittest.TestCase):
    def test_metrics(self) -> None:
        runs = [
            _run("a", checks=[Check("tool_selection", True)], latency_ms=100, usage={"input_tokens": 10, "output_tokens": 2}),
            _run("a", status="fail", checks=[Check("tool_selection", False)], latency_ms=300),
            _run("b", "off_topic", checks=[Check("tool_selection", True)], latency_ms=200,
                 judge={"verdict": "pass", "reason": "ok"}, judge_usage={"input_tokens": 50, "output_tokens": 5}),
            _run("c", "off_topic", status="error", error={"kind": "timeout", "message": "t"}),
        ]
        s = aggregate(runs)
        self.assertEqual((s["runs"], s["scored"], s["passed"], s["errors"]), (4, 3, 2, 1))
        self.assertEqual(s["pass_rate"], 0.6667)
        self.assertEqual(s["tool_selection_accuracy"], 0.6667)
        self.assertEqual(s["by_category"]["off_topic"], {"runs": 2, "passed": 1, "errors": 1, "pass_rate": 1.0})
        self.assertEqual(s["latency_ms"], {"p50": 200, "p95": 300})
        self.assertEqual(s["errors_by_kind"], {"timeout": 1})
        self.assertEqual(s["flaky_cases"], ["a"])
        self.assertEqual(s["judge"], {"judged": 1, "passed": 1, "errors": 0})
        self.assertEqual(s["tokens"]["judge_total_input"], 50)
        self.assertEqual(aggregate([])["pass_rate"], 0.0)

    def test_percentile(self) -> None:
        self.assertIsNone(percentile([], 50))
        self.assertEqual(percentile([5], 95), 5)
        self.assertEqual(percentile(list(range(1, 101)), 95), 95)

    def test_report_shape(self) -> None:
        meta = {"model": "fake:m", "judge_model": None, "dataset_version": "sha256:abc", "cases_total": 3,
                "cases_selected": 2, "repeat": 2, "started_at": "t", "duration_s": 1.0}
        runs = [_run("a"), _run("a", status="fail", question="q?", answer="x", checks=[Check("judge", False, "made up")]),
                _run("b", status="error", error={"kind": "upstream", "message": "boom"})]
        report = build_report(meta, runs)
        self.assertEqual(set(report), {"meta", "summary", "cases", "runs"})
        self.assertEqual([(c["id"], c["status"]) for c in report["cases"]], [("a", "flaky"), ("b", "error")])
        self.assertEqual(report["cases"][0]["failed_checks"], ["judge"])
        json.dumps(report)
        markdown = render_markdown(report)
        for text in ("fake:m", "sha256:abc", "| a | current_weather | FLAKY | 1/2 | judge |", "made up", "upstream: boom"):
            self.assertIn(text, markdown)


class FakeJudge(ToolCallingFakeModel):
    """Returns a fixed verdict through with_structured_output."""

    verdict: str = "pass"

    def with_structured_output(self, schema, include_raw=False, **kwargs):
        raw = AIMessage(content="", usage_metadata={"input_tokens": 40, "output_tokens": 8, "total_tokens": 48})
        return RunnableLambda(lambda _: {"raw": raw, "parsed": Verdict(reason="grounded", verdict=self.verdict), "parsing_error": None})


class RunCaseTests(unittest.IsolatedAsyncioTestCase):
    CASE = next(c for c in DATASET.cases if c.id == "ndc-san-francisco")

    def setUp(self) -> None:
        ai_service._get_agent.cache_clear()
        ai_service._run_slots.cache_clear()
        self.addCleanup(ai_service._get_agent.cache_clear)
        self.addCleanup(ai_service._run_slots.cache_clear)

    async def _run_case(self, model, judge=None, case=None):
        ai_service._get_agent.cache_clear()  # the compiled agent holds the previous model
        with patch.object(ai_service, "get_chat_model", return_value=model), patched_weather_services():
            return await eval_run.run_case(case or self.CASE, DATASET, repeat=0, judge=judge, today=TODAY, run_id="r")

    def _sf_model(self, answer="It is 16.4 °C in San Francisco."):
        return _model(
            _tool_call("get_current_weather", {"city": "San Francisco", "country_code": "US"}),
            AIMessage(content=answer, usage_metadata={"input_tokens": 30, "output_tokens": 9, "total_tokens": 39}),
        )

    async def test_real_agent_runs_against_fixtures(self) -> None:
        result = await self._run_case(self._sf_model(), judge=FakeJudge(messages=iter([])))

        self.assertEqual(result.status, "pass", result.checks)
        self.assertEqual(result.tool_calls, [{"name": "get_current_weather", "args": {"city": "San Francisco", "country_code": "US"}}])
        # The judge sees exactly what the model saw from the tool.
        [tool_result] = result.tool_results
        self.assertEqual(tool_result["status"], "success")
        self.assertIn("16.4 °C", tool_result["content"])
        self.assertIn("United States", tool_result["content"])
        self.assertEqual(result.judge, {"reason": "grounded", "verdict": "pass"})
        self.assertEqual(result.judge_usage, {"input_tokens": 40, "output_tokens": 8})
        self.assertEqual(result.usage, {"input_tokens": 30, "output_tokens": 9})

    async def test_wrong_country_is_not_found_and_fails(self) -> None:
        model = _model(_tool_call("get_current_weather", {"city": "San Francisco"}), AIMessage(content="It is 16 °C."))
        result = await self._run_case(model)

        self.assertEqual(result.status, "fail")
        self.assertIn("No location found", result.tool_results[0]["content"])
        failed = {c.name for c in result.checks if not c.passed}
        self.assertEqual(failed, {"tool_args"})

    async def test_judge_verdict_and_judge_errors(self) -> None:
        result = await self._run_case(self._sf_model(), judge=FakeJudge(messages=iter([]), verdict="fail"))
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.checks[-1].name, "judge")

        broken = FakeJudge(messages=iter([]))
        with patch.object(eval_run, "judge_answer", AsyncMock(side_effect=RuntimeError("judge down"))):
            result = await self._run_case(self._sf_model(), judge=broken)
        self.assertEqual(result.status, "pass")
        self.assertIn("judge down", result.judge["error"])

    async def test_rate_limit_is_retried_once_then_recorded_as_error(self) -> None:
        ask = AsyncMock(side_effect=[AIRateLimitError("busy", retry_after=3), AIRateLimitError("busy", retry_after=3)])
        with patch.object(ai_service, "ask_weather_assistant", ask), patch("asyncio.sleep", AsyncMock()) as sleep:
            result = await eval_run.run_case(self.CASE, DATASET, repeat=0, judge=None, today=TODAY, run_id="r")
        sleep.assert_awaited_once_with(3)
        self.assertEqual((result.status, result.attempts, result.error["kind"]), ("error", 2, "rate_limited"))

        with patch.object(ai_service, "ask_weather_assistant", AsyncMock(side_effect=AIUpstreamError("bad gateway"))):
            result = await eval_run.run_case(self.CASE, DATASET, repeat=0, judge=None, today=TODAY, run_id="r")
        self.assertEqual((result.status, result.error["kind"]), ("error", "upstream"))

    async def test_judge_messages_include_fixtures_and_notes(self) -> None:
        case = next(c for c in DATASET.cases if c.id == "cmp-nairobi-mombasa")
        system, user = judge_messages(DATASET, case, "answer", [], TODAY)
        self.assertIn("Mombasa (29.1 °C) is warmer", system["content"])
        evidence = json.loads(user["content"])
        self.assertEqual(evidence["fixture_data"]["locations"]["mombasa"]["current"]["temperature_2m"], 29.1)
        self.assertEqual(evidence["tools_available_to_assistant"], ["get_current_weather"])


class MainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ai_service._get_agent.cache_clear()
        ai_service._run_slots.cache_clear()
        self.addCleanup(ai_service._get_agent.cache_clear)
        self.addCleanup(ai_service._run_slots.cache_clear)

    async def test_end_to_end_report_and_exit_code(self) -> None:
        class Decliner(ToolCallingFakeModel):
            def _generate(self, messages, *args, **kwargs):
                self.messages = iter([AIMessage(content="Sorry, I can only help with weather questions.")])
                return super()._generate(messages, *args, **kwargs)

        model = Decliner(messages=iter([]))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            args = eval_run.parse_args(
                ["--cases", "off_topic", "--no-judge", "--concurrency", "50", "--out", str(out), "--fail-under", "1",
                 "--model", "fake:model"]
            )
            with (
                patch.object(ai_service, "get_chat_model", return_value=model),
                patch.object(eval_run, "get_chat_model", return_value=model),
                patch.object(ai_service.settings, "ai_max_concurrency", 2),
                patch("sys.stderr"),
                patch("builtins.print"),
            ):
                code = await eval_run.main_async(args)
            report = json.loads(out.read_text())
            markdown = out.with_suffix(".md").read_text()

        # "What is 17 times 23?" and friends are all declined.
        self.assertEqual(code, 0, report["summary"])
        self.assertEqual(report["meta"]["concurrency"], 2)
        self.assertEqual(report["meta"]["model"], "fake:model")
        self.assertEqual(report["summary"]["by_category"]["off_topic"]["pass_rate"], 1.0)
        self.assertIn("Pass rate: 100%", markdown)

    def test_arguments_are_validated(self) -> None:
        for argv in (["--fail-under", "80"], ["--repeat", "0"], ["--concurrency", "0"]):
            with self.subTest(argv), patch("sys.stderr"), self.assertRaises(SystemExit):
                eval_run.parse_args(argv)
