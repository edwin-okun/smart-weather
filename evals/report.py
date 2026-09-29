"""Eval report: a JSON document plus a Markdown rendering of it."""

from dataclasses import asdict
from typing import Any

from evals.scoring import RunResult, aggregate


def build_report(meta: dict[str, Any], runs: list[RunResult]) -> dict[str, Any]:
    cases: dict[str, dict[str, Any]] = {}
    for run in runs:
        row = cases.setdefault(
            run.case_id,
            {"id": run.case_id, "category": run.category, "runs": 0, "passed": 0, "errors": 0, "failed_checks": []},
        )
        row["runs"] += 1
        row["passed"] += run.status == "pass"
        row["errors"] += run.status == "error"
        for check in run.checks:
            if not check.passed and check.name not in row["failed_checks"]:
                row["failed_checks"].append(check.name)
    for row in cases.values():
        scored = row["runs"] - row["errors"]
        if scored == 0:
            row["status"] = "error"
        elif row["passed"] == scored:
            row["status"] = "pass"
        else:
            row["status"] = "fail" if row["passed"] == 0 else "flaky"
    return {
        "meta": meta,
        "summary": aggregate(runs),
        "cases": list(cases.values()),
        "runs": [asdict(run) for run in runs],
    }


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate:.0%}"


def _cell(text: Any, width: int = 90) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= width else text[: width - 1] + "…"


def render_summary(report: dict[str, Any]) -> str:
    """Header, aggregate metrics and the per-case table (what the terminal shows)."""
    meta, s = report["meta"], report["summary"]
    tokens, latency = s["tokens"], s["latency_ms"]
    lines = [
        "# Weather agent eval",
        "",
        f"- Model: `{meta['model']}`; judge: `{meta['judge_model'] or 'off'}`",
        f"- Dataset: `{meta['dataset_version']}` ({meta['cases_selected']} of {meta['cases_total']} cases, "
        f"repeat {meta['repeat']})",
        f"- Started: {meta['started_at']} ({meta['duration_s']}s)",
        "",
        f"**Pass rate: {_pct(s['pass_rate'])}** ({s['passed']}/{s['scored']} scored runs; "
        f"{s['errors']} errors excluded)",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Tool selection accuracy | {_pct(s['tool_selection_accuracy'])} |",
        *(f"| Check `{name}` | {_pct(c['rate'])} ({c['passed']}/{c['evaluated']}) |" for name, c in s["checks"].items()),
        f"| Judge | {s['judge']['passed']}/{s['judge']['judged']} pass, {s['judge']['errors']} errors |",
        f"| Mean tokens per run (in / out) | {tokens['mean_input']} / {tokens['mean_output']} |",
        f"| Total agent tokens (in / out) | {tokens['total_input']} / {tokens['total_output']} |",
        f"| Total judge tokens (in / out) | {tokens['judge_total_input']} / {tokens['judge_total_output']} |",
        f"| Latency p50 / p95 | {latency['p50']} ms / {latency['p95']} ms |",
        f"| Errors | {s['errors']} {s['errors_by_kind'] or ''} |",
        f"| Flaky cases | {', '.join(s['flaky_cases']) or 'none'} |",
        "",
        "| Category | Pass rate | Passed | Runs | Errors |",
        "| --- | --- | --- | --- | --- |",
        *(
            f"| {name} | {_pct(c['pass_rate'])} | {c['passed']} | {c['runs']} | {c['errors']} |"
            for name, c in s["by_category"].items()
        ),
        "",
        "| Case | Category | Status | Passed | Failed checks |",
        "| --- | --- | --- | --- | --- |",
        *(
            f"| {c['id']} | {c['category']} | {c['status'].upper()} | {c['passed']}/{c['runs'] - c['errors']} "
            f"| {', '.join(c['failed_checks'])} |"
            for c in report["cases"]
        ),
    ]
    return "\n".join(lines) + "\n"


def render_markdown(report: dict[str, Any]) -> str:
    """The summary plus details of every failed or errored run."""
    lines = [render_summary(report), "## Failures and errors", ""]
    problems = [r for r in report["runs"] if r["status"] != "pass"]
    if not problems:
        lines.append("None.")
    for run in problems:
        lines += [f"### {run['case_id']} (run {run['repeat'] + 1}): {run['status'].upper()}", ""]
        lines.append(f"- Question: {_cell(run['question'], 300)}")
        if run["error"]:
            lines.append(f"- Error: {run['error']['kind']}: {_cell(run['error']['message'], 300)}")
        else:
            lines.append(f"- Answer: {_cell(run['answer'], 600)}")
            calls = ", ".join(f"{c['name']}({c['args']})" for c in run["tool_calls"]) or "none"
            lines.append(f"- Tool calls: {_cell(calls, 300)}")
            for check in run["checks"]:
                if not check["passed"]:
                    lines.append(f"- Failed `{check['name']}`: {_cell(check['detail'], 300)}")
        lines.append("")
    return "\n".join(lines)
