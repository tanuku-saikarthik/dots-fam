"""Model routing: every Dot names `provider:model`.

Put a strong reasoning model on the Chief of Staff and a fast, cheap one on the
specialists. Providers: openai, anthropic, openrouter.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from .config import Settings

PROVIDERS = ("openai", "anthropic", "openrouter")
KEY_NAMES = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
_REF = re.compile(r"^(openai|anthropic|openrouter):([A-Za-z0-9._/:@+-]{1,160})$")

SUGGESTED_MODELS = [
    "anthropic:claude-opus-4-5",
    "anthropic:claude-sonnet-4-5",
    "anthropic:claude-haiku-4-5",
    "openai:gpt-5.2",
    "openai:gpt-5-mini",
    "openrouter:anthropic/claude-sonnet-4.5",
    "openrouter:openai/gpt-5-mini",
]


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


class ModelSetupError(RuntimeError):
    pass


def parse_ref(value: str) -> ModelRef:
    match = _REF.match(value.strip())
    if not match:
        raise ValueError("Write models as provider:model, e.g. anthropic:claude-sonnet-4-5.")
    return ModelRef(match.group(1), match.group(2))


def provider_key(settings: Settings, provider: str) -> str | None:
    return {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
    }.get(provider)


def configured_providers(settings: Settings) -> list[str]:
    return [provider for provider in PROVIDERS if provider_key(settings, provider)]


def resolve(settings: Settings, override: str | None = None) -> ModelRef:
    value = override or settings.default_model
    if not value:
        raise ModelSetupError("Set DEFAULT_MODEL (e.g. anthropic:claude-sonnet-4-5).")
    ref = parse_ref(value)
    if not provider_key(settings, ref.provider):
        raise ModelSetupError(f"{KEY_NAMES[ref.provider]} is needed for {ref}.")
    return ref


def missing_setup(settings: Settings) -> list[str]:
    """What .env still needs before the team can work (the Chief's and specialists' models)."""
    if not settings.default_model:
        return ["DEFAULT_MODEL"]
    missing: list[str] = []
    for name, ref in (
        ("DEFAULT_MODEL", settings.default_model),
        ("WORKER_MODEL", settings.worker_model),
    ):
        if not ref:
            continue
        try:
            provider = parse_ref(ref).provider
        except ValueError:
            missing.append(name)
            continue
        if not provider_key(settings, provider) and KEY_NAMES[provider] not in missing:
            missing.append(KEY_NAMES[provider])
    return missing


def build_chat_model(settings: Settings, ref: ModelRef, max_tokens: int = 4096) -> BaseChatModel:
    key = provider_key(settings, ref.provider)
    if not key:
        raise ModelSetupError(f"{KEY_NAMES[ref.provider]} is not set.")
    if ref.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=ref.model, api_key=key, max_tokens=max_tokens, max_retries=2)
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {"model": ref.model, "api_key": key, "max_retries": 2}
    if ref.provider == "openrouter":
        kwargs["base_url"] = settings.openrouter_base_url
        kwargs["max_tokens"] = max_tokens
    else:
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        kwargs["max_completion_tokens"] = max_tokens
    return ChatOpenAI(**kwargs)


# Tests (and future local models) swap this factory out.
ModelFactory = Callable[[Settings, ModelRef, dict[str, Any]], BaseChatModel]


def default_factory(settings: Settings, ref: ModelRef, dot: dict[str, Any]) -> BaseChatModel:
    return build_chat_model(settings, ref, max_tokens=6000 if dot.get("can_delegate") else 4096)
