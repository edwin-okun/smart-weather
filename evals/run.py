"""Run the /ai/ask eval dataset against the real agent and a real chat model.

    uv run python -m evals.run --cases 'cw-*,off_topic' --no-judge

Weather tools are served from fixtures (no Open-Meteo, no database); every
model call is real and costs tokens. See evals/README.md.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tracers.context import collect_runs
from langsmith.run_helpers import tracing_context

from app.ai.models import get_chat_model
from app.ai.tracing import configure_tracing, flush_traces
from app.config import settings
from app.exceptions import AIRateLimitError, AIServiceError, AIStepLimitError, AITimeoutError
from app.schemas.auth import AuthenticatedClient
from app.services import ai_service
from evals.backends import CaseBackend, patched_weather_services, use_backend
from evals.dataset import Case, Dataset, load_dataset, select_cases
from evals.judge import judge_answer
from evals.report import build_report, render_markdown, render_summary
from evals.scoring import Check, RunResult, score_run

RESULTS_DIR = Path(__file__).parent / "results"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", help="comma-separated case ids, globs (e.g. 'cw-*') or category names")
    parser.add_argument("--limit", type=int, help="run at most N cases (after --cases)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case, to expose flakiness (default 1)")
    parser.add_argument(
        "--concurrency", type=int, default=4, help="cases in flight; capped at AI_MAX_CONCURRENCY (default 4)"
    )
    parser.add_argument("--model", help="provider:model for the agent (default: AI_MODEL)")
    parser.add_argument("--judge-model", help="provider:model for the LLM judge (default: the agent model)")
    parser.add_argument("--no-judge", action="store_true", help="code checks only")
    parser.add_argument("--out", type=Path, help="report JSON path; a .md is written next to it")
    parser.add_argument("--fail-under", type=float, metavar="RATE", help="exit 1 if the pass rate (0-1) is below RATE")
    parser.add_argument("-v", "--verbose", action="store_true", help="show app warnings and tool error tracebacks")
    args = parser.parse_args(argv)
    for name in ("limit", "repeat", "concurrency"):
        value = getattr(args, name)
        if value is not None and value < 1:
            parser.error(f"--{name} must be at least 1")
    if args.fail_under is not None and not 0 <= args.fail_under <= 1:
        parser.error("--fail-under must be between 0 and 1")
    return args


def _tool_results(runs) -> list[dict[str, Any]]:
    """Tool messages the model actually saw, from the agent's root run output."""
    for run in runs:
        if run.name == "ask_weather_assistant" and run.outputs:
            messages = run.outputs.get("messages", [])
            args_by_id = {
                call["id"]: call["args"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls
            }
            return [
                {
                    "tool": m.name,
                    "args": args_by_id.get(m.tool_call_id, {}),
                    "status": m.status,
                    "content": m.text,
                }
                for m in messages
                if isinstance(m, ToolMessage)
            ]
    return []


async def run_case(
    case: Case,
    dataset: Dataset,
    *,
    repeat: int,
    judge: BaseChatModel | None,
    today: date,
    run_id: str,
) -> RunResult:
    result = RunResult(case_id=case.id, category=case.category, repeat=repeat, question=case.question)
    client = AuthenticatedClient(id=0, client_id=f"eval:{case.id}", name="eval", scopes=set(case.scopes))
    backend = CaseBackend(dataset, case, today)
    # Tags and metadata reach LangSmith only when tracing is configured.
    metadata = {"eval_run_id": run_id, "eval_case_id": case.id, "eval_category": case.category, "eval_repeat": repeat}

    while True:
        result.attempts += 1
        started = time.perf_counter()
        try:
            with (
                tracing_context(tags=["eval"], metadata=metadata),
                use_backend(backend),
                collect_runs() as collector,
            ):
                response = await ai_service.ask_weather_assistant(case.question, client)
            break
        except AIRateLimitError as exc:
            if result.attempts == 1:
                await asyncio.sleep(exc.retry_after)
                continue
            result.error = {"kind": "rate_limited", "message": str(exc)}
        except AIStepLimitError as exc:
            # The agent looped until the step cap: a behaviour failure, not an outage.
            result.checks = [Check("completed", False, str(exc))]
        except AITimeoutError as exc:
            result.error = {"kind": "timeout", "message": str(exc)}
        except AIServiceError as exc:
            result.error = {"kind": "upstream", "message": str(exc)}
        result.latency_ms = round((time.perf_counter() - started) * 1000)
        return result.finalize()

    result.latency_ms = round((time.perf_counter() - started) * 1000)
    result.answer = response.answer
    result.tool_calls = [call.model_dump() for call in response.tool_calls]
    result.tool_results = _tool_results(collector.traced_runs)
    result.usage = response.usage.model_dump()
    result.checks = score_run(case, response.answer, result.tool_calls, dataset.canaries_for(case), today)

    if judge is not None:
        try:
            with tracing_context(tags=["eval", "eval-judge"], metadata={"eval_run_id": run_id, "eval_case_id": case.id}):
                verdict, result.judge_usage = await judge_answer(
                    judge, dataset, case, response.answer, result.tool_results, today
                )
        except Exception as exc:  # a judge failure must not sink the run
            result.judge = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        else:
            result.judge = verdict.model_dump()
            result.checks.append(Check("judge", verdict.verdict == "pass", verdict.reason))
    return result.finalize()


async def run_all(
    cases: list[Case],
    dataset: Dataset,
    *,
    repeat: int,
    concurrency: int,
    judge: BaseChatModel | None,
    run_id: str,
    today: date,
) -> list[RunResult]:
    slots = asyncio.Semaphore(concurrency)
    jobs = [(case, r) for case in cases for r in range(repeat)]
    done = 0

    async def one(case: Case, r: int) -> RunResult:
        nonlocal done
        async with slots:
            result = await run_case(case, dataset, repeat=r, judge=judge, today=today, run_id=run_id)
        done += 1
        extra = result.error["kind"] if result.error else ", ".join(c.name for c in result.checks if not c.passed)
        print(f"[{done}/{len(jobs)}] {result.status.upper():5} {case.id} {extra}".rstrip(), file=sys.stderr)
        return result

    return list(await asyncio.gather(*(one(case, r) for case, r in jobs)))


def _default_out(model: str, started: datetime) -> Path:
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", model).strip("-")
    return RESULTS_DIR / f"{started:%Y%m%dT%H%M%S}-{slug}.json"


async def main_async(args: argparse.Namespace) -> int:
    dataset = load_dataset()
    cases = select_cases(dataset.cases, args.cases, args.limit)
    if not cases:
        print("No cases match --cases.", file=sys.stderr)
        return 2

    model_name = args.model or settings.ai_model
    judge_name = None if args.no_judge else (args.judge_model or model_name)
    # Stay under the app's own concurrency cap, or runs would be rejected with 429.
    concurrency = min(args.concurrency, settings.ai_max_concurrency)
    if concurrency < args.concurrency:
        print(f"--concurrency capped at AI_MAX_CONCURRENCY={concurrency}", file=sys.stderr)
    tracing = configure_tracing()
    started_at = datetime.now(UTC)
    run_id = uuid.uuid4().hex[:12]
    today = date.today()

    # Scoped override so the agent, its logs and its trace metadata all use the eval model.
    with patch.object(settings, "ai_model", model_name), patched_weather_services():
        ai_service._get_agent.cache_clear()
        try:
            get_chat_model()
            judge = get_chat_model(judge_name) if judge_name else None
        except Exception as exc:  # e.g. missing API key or provider package
            print(f"Could not build the chat model: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        runs = await run_all(
            cases, dataset, repeat=args.repeat, concurrency=concurrency, judge=judge, run_id=run_id, today=today
        )
        ai_service._get_agent.cache_clear()

    meta = {
        "run_id": run_id,
        "model": model_name,
        "judge_model": judge_name,
        "dataset_version": dataset.version,
        "cases_total": len(dataset.cases),
        "cases_selected": len(cases),
        "repeat": args.repeat,
        "concurrency": concurrency,
        "started_at": started_at.isoformat(timespec="seconds"),
        "duration_s": round((datetime.now(UTC) - started_at).total_seconds(), 1),
        "langsmith": tracing,
    }
    report = build_report(meta, runs)
    out = args.out or _default_out(model_name, started_at)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")

    print(render_summary(report))
    print(f"Report: {out} (+ {out.with_suffix('.md').name})")
    if tracing:
        await asyncio.to_thread(flush_traces)

    pass_rate = report["summary"]["pass_rate"]
    if args.fail_under is not None and pass_rate < args.fail_under:
        print(f"Pass rate {pass_rate:.1%} is below --fail-under {args.fail_under:.1%}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if not args.verbose:
        # Tool-failure cases trigger logged tracebacks by design; keep the output readable.
        logging.getLogger("app").setLevel(logging.CRITICAL)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
