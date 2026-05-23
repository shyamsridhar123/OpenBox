from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_KEY_FILE = Path(r"C:\Users\shyamsridhar\code\openbox\examples\.opensandbox-api-key")


def _read_key_file() -> str:
    try:
        return _KEY_FILE.read_text().strip()
    except Exception:
        return ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    CONTROL_PLANE_URL: str = "http://localhost:18080"
    CONTROL_PLANE_API_KEY: str = ""
    OPENSANDBOX_NAMESPACE: str = "opensandbox"

    def model_post_init(self, __context: object) -> None:
        if not self.CONTROL_PLANE_API_KEY:
            object.__setattr__(self, "CONTROL_PLANE_API_KEY", _read_key_file())


settings = Settings()
