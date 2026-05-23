from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./ceremoni.db"
    openai_api_key: str = ""
    azure_speech_key: str = ""
    azure_speech_region: str = "eastus"
    storage_path: str = "./storage"
    tts_voice: str = "en-US-GuyNeural"
    whisper_model: str = "base"

    # Microsoft Graph API (Forms sync)
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_form_id: str = ""
    ms_form_owner: str = ""  # UPN/email of whoever created the form

    # Admin auth
    admin_emails: str = ""  # comma-separated

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
