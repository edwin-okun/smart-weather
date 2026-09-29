from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "smart-weather"
    database_url: str = "sqlite://smart_weather.sqlite3"
    generate_db_schemas: bool = True
    weather_client_timeout: float = 10.0
    access_token_ttl_seconds: int = 900
    authorization_code_ttl_seconds: int = 300
    refresh_token_ttl_seconds: int = 2_592_000
    public_base_url: str | None = None

    # Weather history: each API client sees only its own lookups, for this many
    # days. Older rows are hidden from history immediately and deleted in bounded
    # batches (weather_history_prune_batch_size rows) each time a lookup is saved.
    weather_history_retention_days: PositiveInt = 30
    weather_history_prune_batch_size: PositiveInt = 500

    # AI: LangChain "provider:model" string; api key falls back to openai_api_key
    # for openai models. ai_timeout/ai_max_retries apply per model call, while
    # ai_request_timeout bounds the whole /ai/ask run. ai_max_steps is the agent
    # graph recursion limit (each model call and each tool round is one step).
    ai_model: str = "openai:gpt-4o-mini"
    ai_api_key: str | None = None
    ai_temperature: float = 0.0
    ai_max_output_tokens: int = 1024
    ai_timeout: float = 30.0
    ai_max_retries: int = 1
    ai_request_timeout: float = 60.0
    ai_max_steps: int = 8
    # Max /ai/ask runs in flight per process. Each run makes several model calls
    # (and retries count against provider rate limits), so this bounds quota use.
    # Excess requests are rejected with 429 rather than queued.
    ai_max_concurrency: PositiveInt = 10

    # LangSmith tracing (off by default). LangChain reads these only from the
    # process environment, so app.ai.tracing exports them at startup; that lets
    # them live in .env like every other setting. Traces contain user questions
    # and model/tool output; set the hide_* flags to redact them.
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "smart-weather"
    langsmith_endpoint: str | None = None
    langsmith_hide_inputs: bool = False
    langsmith_hide_outputs: bool = False

    # api keys
    openai_api_key: str | None = None


settings = Settings()
