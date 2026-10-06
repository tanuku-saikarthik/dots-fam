"""Runtime settings, read from the environment (and `.env`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8787
    data_dir: Path = Path("data")
    owner_token: str | None = None
    public_url: str | None = None
    default_timezone: str = "UTC"

    # Models: Dots name `provider:model`; these keys enable providers.
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    default_model: str | None = None
    worker_model: str | None = None

    # Web search and crawling for every Dot (exa.ai). Without a key, Dots can still read pages by URL.
    exa_api_key: str | None = None
    exa_base_url: str = "https://api.exa.ai"
    exa_max_calls_per_run: int = Field(default=30, ge=1, le=500)

    # Execution limits.
    runner_concurrency: int = Field(default=3, ge=1, le=16)
    task_timeout_seconds: int = Field(default=600, ge=30, le=7200)
    max_delegated_workers: int = Field(default=4, ge=1, le=16)
    worker_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    web_read_limit_chars: int = 24_000

    # Computers: none | local | docker
    computer_driver: str = "none"
    computer_image: str = "dotsfam-computer:latest"
    computer_token: str | None = None

    # Slack (Socket Mode)
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_allowed_users: str = ""
    slack_dot: str | None = None
    slack_notify_channel: str | None = None

    # Voice calls (Pipecat over WebRTC). local = Whisper + Kokoro on this machine (open models);
    # openai = OpenAI speech-to-text and text-to-speech; off = no calls. The Dot is the brain either way.
    voice_stack: str = "local"
    voice_language: str = "en"
    voice_whisper_model: str = "large-v3-turbo"
    voice_whisper_device: str = "auto"
    voice_kokoro_voice: str = "af_heart"
    voice_openai_stt_model: str = "gpt-4o-transcribe"
    voice_openai_tts_model: str = "gpt-4o-mini-tts"
    voice_openai_voice: str = "alloy"
    voice_ice_servers: str = "stun:stun.l.google.com:19302"

    # Calls: ring your phone when a Dot needs you, for free. Web push to the installed app
    # (keys are generated on first start), and/or an ntfy topic (ntfy.sh app, alarm-style alert).
    vapid_contact: str = "mailto:owner@example.com"
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    ntfy_token: str | None = None

    @field_validator("default_timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(
                "DEFAULT_TIMEZONE must be an IANA time zone, e.g. Asia/Kolkata"
            ) from error
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "dotsfam.sqlite"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def ice_servers(self) -> list[str]:
        return [item.strip() for item in self.voice_ice_servers.split(",") if item.strip()]

    @property
    def slack_user_ids(self) -> list[str]:
        return [item.strip() for item in self.slack_allowed_users.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
