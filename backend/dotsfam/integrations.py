"""Optional integrations: per-Dot computers, Slack, voice. Each one is off unless configured."""

from __future__ import annotations

import logging

from .runtime import Runtime

log = logging.getLogger("dotsfam")


async def attach_integrations(runtime: Runtime) -> None:
    settings = runtime.settings
    if settings.computer_driver != "none":
        from .computers import ComputerManager

        runtime.attach_computers(ComputerManager.create(runtime.store, settings))
    if settings.slack_bot_token and settings.slack_app_token:
        try:
            from .slack import SlackBridge

            runtime.attach_slack(SlackBridge(runtime))
        except ImportError:
            log.warning(
                "Slack is configured but slack-bolt is not installed: pip install 'dotsfam[slack]'"
            )
