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

    # AI: LangChain "provider:model" string; api key falls back to openai_api_key
    # for openai models.
    ai_model: str = "openai:gpt-4o-mini"
    ai_api_key: str | None = None
    ai_temperature: float = 0.0
    ai_timeout: float = 60.0
    ai_max_retries: int = 3
    ai_max_steps: int = 8

    # api keys
    openai_api_key: str | None = None


settings = Settings()
