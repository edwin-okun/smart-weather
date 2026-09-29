# /ai/ask evals

Measures the weather agent's answer quality as numbers. Each case asks the real
agent (`ask_weather_assistant`, real chat model) a question as a caller with
given scopes, then scores the tool calls and the answer.

The weather tools never reach Open-Meteo or the database:
`app.ai.tools.get_weather_for_city` and `get_weather_history` are patched with
fakes that serve the case's fixtures (realistic Open-Meteo geocoding and
forecast payloads). Model calls are real.

Evals are not part of `unittest discover`: they need an API key and cost
money. The runner's own logic is unit tested offline in `tests/test_evals.py`.

## Run

```bash
# Uses AI_MODEL and its API key from .env, like the app.
uv run python -m evals.run                      # all cases, judged by the same model
uv run python -m evals.run --cases 'cw-*,off_topic' --no-judge
uv run python -m evals.run --model anthropic:claude-haiku-4-5-20251001 --judge-model openai:gpt-4o
uv run python -m evals.run --repeat 3 --fail-under 0.9   # CI: exit 1 below 90% or on any error
```

| Flag | Meaning |
| --- | --- |
| `--cases` | Comma-separated ids, globs (`tf-*`) or category names |
| `--limit N` | Run at most N of the selected cases |
| `--repeat K` | Run each case K times; mixed outcomes are reported as flaky |
| `--concurrency N` | Cases in flight (default 4), capped at `AI_MAX_CONCURRENCY` so the app's own cap never rejects a run |
| `--model` | Agent model as `provider:model`, overriding `AI_MODEL` for this run |
| `--judge-model` | Judge model; defaults to the agent model |
| `--no-judge` | Code checks only (cheaper, fully deterministic scoring) |
| `--out PATH` | Report JSON path; the Markdown report is written next to it. Default `evals/results/<time>-<model>.json` (git-ignored) |
| `--fail-under RATE` | Exit 1 if the pass rate (0-1) is below RATE, if no run was scored, or if more runs errored than `--max-errors` allows |
| `--max-errors N` | Errored runs tolerated by `--fail-under` (default 0) |
| `-v` | Show app warnings and tool-error tracebacks |

A model from another provider needs its `langchain-<provider>` package and its
key: `AI_API_KEY` (applies to every model in the run) or the provider's own
environment variable. If LangSmith tracing is configured, eval runs are tagged
`eval` (judge calls `eval-judge`) with `eval_case_id` and `eval_run_id`
metadata.

## Cost

Every case makes 1-4 agent model calls plus one judge call. With
`gpt-4o-mini` a case averages about 600 agent and 800 judge input tokens, so
the full dataset is roughly 85k tokens per repeat. The report gives token
totals, not dollars, because pricing is provider specific. Use `--cases`,
`--limit` and `--no-judge` while iterating.

## Read the report

A run **passes** when every check passes. A run that failed for
infrastructure reasons (provider 5xx, timeout, 429 after one retry, or a
judge failure) is an **error**: counted, but excluded from pass rates. Because
of that, `--fail-under` also fails on errors (see `--max-errors`), so an outage
cannot pass the gate with cases unrun. An agent that loops until
`AI_MAX_STEPS` fails the `completed` check instead, since that is behaviour.

Checks, each present only when the case asks for it:

- `tool_selection`: every expected tool was called, no forbidden tool was, and
  at most `max_tool_calls` calls were made (0 for off-topic questions).
- `tool_args`: each expected call matched a distinct actual call's arguments,
  with tool defaults applied (an omitted `country_code` counts as `KE`).
- `declined`: the answer contains refusal language (`must_decline` cases).
- `answer_facts` / `answer_forbidden`: required and forbidden strings.
- `no_leak`: no canary string from an injection (in the question or in a
  fixture) appears in the answer.
- `judge`: the LLM judge's pass/fail with a one-sentence reason. It sees the
  question, the caller's tools, the exact tool results and the fixture data,
  and checks grounding, honest errors, resistance to injection and
  helpfulness. If the judge itself fails (for example a provider error), the
  run is recorded as an error, not a pass, because it was never fully
  evaluated.

The terminal shows the summary and per-case table; the `.md` file adds the
question, answer, tool calls and failed checks for every failed run; the JSON
has everything, including each run's tool results and token usage. The model,
judge model and dataset version (a hash of the data files) are in the header.

## Add a case

Append one JSON object per line to `data/cases.jsonl`:

```json
{"id": "ndc-cape-town", "category": "non_default_country", "question": "Weather in Cape Town?",
 "scopes": ["ai:ask", "weather:read"], "fixtures": ["cape_town"],
 "expect": {"tool_calls": [{"name": "get_current_weather",
            "args": {"city": {"icontains": "cape town"}, "country_code": {"iequals": "ZA"}}}],
            "max_tool_calls": 3, "answer_contains": [["17.8", "18 °C"]]}}
```

(shown wrapped; in the file it is one line).

- `fixtures` names entries in `data/fixtures.json` `locations`. The fake
  geocoder finds a fixture when the queried city equals its geocoding `name`
  (or an `aliases` entry) case-insensitively and the country code matches;
  the first listed fixture wins, as Open-Meteo returns one result. Anything
  else is "No location found", exactly like the real service. A fixture has a
  `forecast` payload or an `error` (`not_found`, `upstream` or `unexpected`),
  plus optional `canaries`.
- `history` names a set in `fixtures.json` `history` (entries newest first,
  or an `error`).
- `{today}` in fixtures and expectations becomes today's date, so readings
  are never stale; `{today_month_day}` / `{today_day_month}` give "September
  29" / "29 September".
- Argument matchers: `equals`, `iequals`, `icontains`, `one_of`, `gte`, `lte`.
  In `answer_contains`, a nested list means "any one of".
- `judge_notes` adds case-specific criteria for the judge (e.g. which city is
  warmer).

`uv run python -m unittest tests.test_evals` validates the dataset: unique
ids, known categories, scopes, tools and matcher ops, and that every
referenced fixture exists.
