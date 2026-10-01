"""Tools a Dot can call. Each tool may carry approval metadata:

- metadata["external"] = True   → always needs owner approval in reversible mode
- metadata["classify"] = fn     → fn(args) returns a reason when this call changes the outside world
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from ..context import DotContext
from .pages import page_tools
from .team import team_tools
from .web import web_tools


def build_tools(ctx: DotContext) -> list[BaseTool]:
    tools: list[BaseTool] = [*page_tools(ctx)]
    if ctx.dot.get("research_allowed") and ctx.store.flags()["research_allowed"]:
        tools += web_tools(ctx)
    if ctx.computers and ctx.computers.enabled_for(ctx.dot):
        tools += ctx.computers.tools(ctx)
    tools += team_tools(ctx)
    slack = ctx.extra.get("slack")
    if slack is not None:
        tools += slack.tools(ctx)
    return tools


def approval_reason(ctx: DotContext, tool: BaseTool | None, args: dict) -> str | None:
    """Why this call needs owner approval under the Reversibility Law, or None."""
    if not ctx.reversible or tool is None:
        return None
    metadata = tool.metadata or {}
    if metadata.get("external"):
        return str(metadata.get("reason") or f"{tool.name} changes something outside Dots Fam")
    classify = metadata.get("classify")
    if callable(classify):
        try:
            return classify(args)
        except Exception:  # noqa: BLE001 - when unsure, ask the owner
            return f"{tool.name} may change something outside Dots Fam"
    return None
