"""Optional integrations: per-Dot computers, Slack, voice, phone. Each one is off unless configured."""

from __future__ import annotations

import logging

from .runtime import Runtime

log = logging.getLogger("dotsfam")


async def attach_integrations(runtime: Runtime) -> None:
    settings = runtime.settings
    if settings.computer_driver != "none":
        from .computers import ComputerManager

        runtime.attach_computers(ComputerManager.create(runtime.store, settings))
    if settings.voice_stack != "off":
        from .voice import VoiceManager  # Pipecat itself is imported only when a call starts

        runtime.attach_voice(VoiceManager(runtime))
    if settings.phone_enabled:
        from .phone import PhoneManager  # Twilio calls into the voice pipeline

        runtime.attach_phone(PhoneManager(runtime))
        if runtime.voice is None:
            log.warning("Twilio is configured but VOICE_STACK=off, so phone calls stay off")
    from .notify import Notifier  # free phone alerts: web push and/or ntfy

    runtime.attach_notifier(Notifier(runtime))
    if settings.slack_bot_token and settings.slack_app_token:
        try:
            from .slack import SlackBridge

            runtime.attach_slack(SlackBridge(runtime))
        except ImportError:
            log.warning(
                "Slack is configured but slack-bolt is not installed: pip install 'dotsfam[slack]'"
            )
