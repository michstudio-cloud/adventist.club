from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = "Adventist Club API"
    ENV: str = "development"
    DATABASE_URL: str
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    PUBLIC_WEB_URL: str = "http://localhost:5173"
    CORS_ORIGINS: str = "http://localhost:5173,https://conquistadores.app"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.CORS_ORIGINS.split(",") if x.strip()]

settings = Settings()
