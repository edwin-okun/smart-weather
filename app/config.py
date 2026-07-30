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


settings = Settings()
