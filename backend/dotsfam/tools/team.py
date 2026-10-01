"""Chief of Staff delegation tool."""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..context import DotContext


class Assignment(BaseModel):
    dot: str = Field(description="Name of the specialist on your roster.", max_length=80)
    brief: str = Field(
        min_length=10,
        max_length=6000,
        description="Self-contained brief: goal, scope, sources, constraints. "
        "The specialist sees only this, never your conversation.",
    )
    expected_output: str = Field("", max_length=1000, description="Exact shape of the deliverable.")


class Delegate(BaseModel):
    assignments: list[Assignment] = Field(min_length=1, max_length=5)
    wait_seconds: int = Field(
        120,
        ge=5,
        le=240,
        description="How long to wait now. Longer work keeps running; results arrive in this "
        "conversation as a follow-up.",
    )


def roster_text(ctx: DotContext) -> str:
    lines = []
    for dot in ctx.store.dots():
        if dot["id"] == ctx.dot["id"]:
            continue
        role = " ".join(dot["instructions"].split())[:260]
        title = f" ({dot['title']})" if dot["title"] else ""
        lines.append(f"- {dot['name']}{title}: {role}")
    return "\n".join(lines) or "- (no specialists yet)"


def team_tools(ctx: DotContext) -> list[BaseTool]:
    if not ctx.dot.get("can_delegate") or ctx.worker or not ctx.delegations:
        return []
    manager = ctx.delegations

    async def delegate_tasks(assignments: list[Assignment], wait_seconds: int = 120) -> dict:
        ctx.check()
        return await manager.dispatch(
            ctx.dot,
            ctx.thread_id,
            [item.model_dump() for item in assignments],
            wait_seconds=wait_seconds,
            cancelled=ctx.cancelled,
        )

    return [
        StructuredTool.from_function(
            coroutine=delegate_tasks,
            name="delegate_tasks",
            args_schema=Delegate,
            description="Hand scoped work to specialist Dots in parallel (1-5 assignments). Each runs on "
            "its own model, tools and computer, sees only its brief, and returns a deliverable.\n"
            f"Roster:\n{roster_text(ctx)}",
        )
    ]
