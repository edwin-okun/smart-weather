# LangChain Weather Agent

## Problem

The service can return current weather for a city, but it has no way to answer
natural-language weather questions. The only AI code was an unused,
OpenAI-specific single-shot helper with a hard-coded pricing table. It could
not call tools and tied the service to one provider.

## Intended outcome

An authorized client can `POST` a weather question and receive an answer
grounded in real weather data. An LLM agent decides which weather tools to
call. The model provider is a configuration choice, not a code dependency.

## Behavior

### Endpoint

`POST /ai/ask` accepts `{"question": string}` (1 to 2000 characters) and
returns:

- `answer`: the assistant's final text;
- `tool_calls`: the ordered `{name, args}` trace of tools the agent called; and
- `usage`: `{input_tokens, output_tokens}` summed across all model calls in
  the request.

The endpoint requires a bearer token with the `ai:ask` scope. A missing token
returns `401`, a token without `ai:ask` returns `403`, and an invalid body
returns `422`. Provider or agent failures return `502` with a generic message.

Each request is independent. No conversation state is stored.

### Agent

The agent is a LangChain `create_agent` loop. The model may call tools, read
their results, and call more tools before answering. The loop is bounded by
`AI_MAX_STEPS` (default `8`).

The system prompt instructs the model to:

- fetch weather with tools and never guess current conditions;
- answer only weather-related questions and decline others;
- report tool errors instead of inventing data; and
- use Celsius and km/h, and know today's date.

### Tools

Tools call the existing weather service layer directly, not HTTP or MCP.

| Tool | Wraps | Required scope |
| --- | --- | --- |
| `get_current_weather(city, country_code="KE")` | `get_weather_for_city` | `weather:read` |
| `list_weather_history(limit=10)` | `get_weather_history` | `weather:history:read` |

Tools are selected per request from the caller's scopes, using the same scope
each equivalent HTTP endpoint requires. The agent therefore cannot read data
the caller could not read directly. A caller with only `ai:ask` gets an agent
with no tools.

`LocationNotFoundError` and `UpstreamServiceError` raised inside a tool are
returned to the model as `{"error": ...}` so it can explain or retry. History
`limit` is clamped to 1 to 100. `get_current_weather` persists the lookup, as
the HTTP endpoint does.

### Model provider

The chat model is built lazily on first use, so the app starts without AI
credentials. It comes from LangChain `init_chat_model` using `AI_MODEL`, a
`provider:model` string such as `openai:gpt-4o-mini`. Switching provider means
changing `AI_MODEL` and installing that provider's LangChain package. The code
contains no provider-specific logic beyond the API-key fallback below.

`AI_API_KEY` is passed to the model when set. For `openai:` models it falls
back to `OPENAI_API_KEY`.

### MCP

`ask_weather_assistant` is excluded from the generated MCP tools. An
MCP-connected LLM should call the weather tools directly, not another LLM that
calls them. `get_weather`, `list_weather_history` and `health` remain exposed.

## Security and operational policy

- `ai:ask` is granted only when explicitly requested. `create-client` defaults
  and dynamic client registration do not include it.
- Tool access is derived from token scopes, not from anything the model or
  request body says.
- Provider exceptions are logged server-side and not returned to the caller.
- The step cap, per-call timeout and retry count bound cost and latency per
  request.
- Token counts are logged and returned. Dollar cost is not computed because
  pricing differs by provider.

## Non-goals

- Conversation memory or multi-turn threads.
- Streaming responses.
- Cost estimation in dollars.
- Additional tools such as forecasts.
- Exposing the agent over MCP.
- Rate limiting or per-client quotas, which remain a deployment concern.

## Acceptance criteria

- `POST /ai/ask` with a token holding `weather:read` and `ai:ask` calls
  `get_current_weather` for "What's the weather in San Francisco?" and returns
  an answer, the tool trace, and token usage.
- Missing token returns `401`; a token without `ai:ask` returns `403`; an empty
  question returns `422`.
- `build_tools` returns no tools for `ai:ask` alone, only the weather tool for
  `weather:read`, and both tools when `weather:history:read` is also present.
- A scripted fake model that emits a tool call and then an answer produces the
  expected answer, trace, and summed usage without network access.
- The app starts with no AI credentials configured.
- `ask_weather_assistant` does not appear in the MCP tool list.
- The full test suite passes.

## Migration and rollback

No database changes. Rollback removes the `/ai/ask` router, the `app/ai`
package and `ai_service`. The removed OpenAI client and service had no
callers.

## Configuration

Add:

- `AI_MODEL`, default `openai:gpt-4o-mini`.
- `AI_API_KEY`, default unset.
- `AI_TEMPERATURE`, default `0.0`.
- `AI_TIMEOUT`, default `60.0` seconds.
- `AI_MAX_RETRIES`, default `3`.
- `AI_MAX_STEPS`, default `8`.

Existing: `OPENAI_API_KEY` is used for `openai:` models when `AI_API_KEY` is
unset.

New dependencies: `langchain-openai`. `mcp` is pinned `<2` because `mcp` 2.x
breaks `fastapi-mcp` 0.4.0.
