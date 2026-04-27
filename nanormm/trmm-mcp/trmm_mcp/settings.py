from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    trmm_api_base: str = Field(..., alias="TRMM_API_BASE")
    trmm_api_token: str = Field(..., alias="TRMM_API_TOKEN")
    policy_path: Path = Field(..., alias="NANORMM_POLICY_PATH")
    redis_url: str = Field(..., alias="NANORMM_REDIS_URL")
    audit_dsn: str = Field(..., alias="NANORMM_AUDIT_DSN")
    pending_action_ttl_seconds: int = Field(1800, alias="NANORMM_PENDING_TTL_SECONDS")

    @field_validator("trmm_api_base")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")
