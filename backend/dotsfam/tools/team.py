"""Team tools: delegating to a specialist, splitting your own work in parallel,
and - for the Chief of Staff - bringing a new specialist onto the team."""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..context import DotContext
from ..models import parse_ref

COLORS = ["purple", "mint", "orange", "blue", "ochre", "rose"]


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


class SubTask(BaseModel):
    brief: str = Field(
        min_length=10,
        max_length=6000,
        description="Self-contained brief for this slice of your current task.",
    )
    expected_output: str = Field(
        "", max_length=1000, description="Exact shape of this slice's output."
    )


class SpawnSubagents(BaseModel):
    tasks: list[SubTask] = Field(
        min_length=1,
        max_length=5,
        description="1-5 independent slices of YOUR OWN current task to run in parallel.",
    )
    wait_seconds: int = Field(120, ge=5, le=240)


class CreateDot(BaseModel):
    name: str = Field(min_length=1, max_length=40, description="Must not match an existing Dot.")
    title: str = Field("", max_length=60, description="Short role label, e.g. 'Contract Reviewer'.")
    instructions: str = Field(
        min_length=10,
        max_length=4000,
        description="Full role card: goal, sources, working style, approval boundary, cadence - "
        "written the same way the owner would write one.",
    )
    model: str | None = Field(
        None, description="provider:model, or leave unset to use the team default."
    )


def roster_text(ctx: DotContext) -> str:
    lines = []
    for dot in ctx.store.dots():
        if dot["id"] == ctx.dot["id"]:
            continue
        role = " ".join(dot["instructions"].split())[:260]
        title = f" ({dot['title']})" if dot["title"] else ""
        local = dot.get("local") or {}
        if local.get("enabled") and local.get("mode") == "build" and local.get("project_dir"):
            role += (
                f" [Codes in {local['project_dir']}: works on its own branch, runs tests, and opens "
                "a pull request.]"
            )
        lines.append(f"- {dot['name']}{title}: {role}")
    return "\n".join(lines) or "- (no specialists yet)"


def team_tools(ctx: DotContext) -> list[BaseTool]:
    if ctx.worker or not ctx.delegations:
        return []  # a spawned worker never gets team tools - no recursive fan-out/delegation
    manager = ctx.delegations
    tools: list[BaseTool] = []

    async def spawn_subagents(tasks: list[SubTask], wait_seconds: int = 120) -> dict:
        ctx.check()
        return await manager.fan_out(
            ctx.dot,
            ctx.thread_id,
            [item.model_dump() for item in tasks],
            wait_seconds=wait_seconds,
            cancelled=ctx.cancelled,
        )

    tools.append(
        StructuredTool.from_function(
            coroutine=spawn_subagents,
            name="spawn_subagents",
            args_schema=SpawnSubagents,
            description="Split YOUR OWN current task into 1-5 independent slices and run them in "
            "parallel, each as a fresh copy of you with no memory of this conversation - only the "
            "slice's brief. Use this to go faster on one task (e.g. research three sources at once), "
            "not to hand work to a different specialist (use delegate_tasks for that).",
        )
    )

    if not ctx.dot.get("can_delegate"):
        return tools

    async def delegate_tasks(assignments: list[Assignment], wait_seconds: int = 120) -> dict:
        ctx.check()
        return await manager.dispatch(
            ctx.dot,
            ctx.thread_id,
            [item.model_dump() for item in assignments],
            wait_seconds=wait_seconds,
            cancelled=ctx.cancelled,
        )

    def classify_new_dot(args: dict) -> str:
        return f'creates a new Dot named "{args.get("name", "")}" on your team'

    async def create_dot(
        name: str, title: str = "", instructions: str = "", model: str | None = None
    ) -> dict:
        ctx.check()
        name = name.strip()
        if not name:
            raise ValueError("Give the new Dot a name.")
        if any(d["name"].lower() == name.lower() for d in ctx.store.dots()):
            raise ValueError(f'"{name}" already exists on the team.')
        if model:
            parse_ref(model)  # raises a clear error for a malformed provider:model string
        used = {d["color"] for d in ctx.store.dots()}
        color = next(
            (c for c in COLORS if c not in used), COLORS[len(ctx.store.dots()) % len(COLORS)]
        )
        dot = ctx.store.create_dot(
            name=name,
            title=title,
            instructions=instructions,
            space_id=ctx.dot["space_id"],
            space_ids=ctx.dot.get("space_ids"),
            model=model,
            can_delegate=False,
            approval_mode="reversible",
            research_allowed=True,
            memory_allowed=True,
            color=color,
        )
        return {"id": dot["id"], "name": dot["name"], "title": dot["title"], "color": dot["color"]}

    tools += [
        StructuredTool.from_function(
            coroutine=delegate_tasks,
            name="delegate_tasks",
            args_schema=Delegate,
            description="Hand scoped work to specialist Dots in parallel (1-5 assignments). Each runs on "
            "its own model, tools and computer, sees only its brief, and returns a deliverable.\n"
            f"Roster:\n{roster_text(ctx)}",
        ),
        StructuredTool.from_function(
            coroutine=create_dot,
            name="create_dot",
            args_schema=CreateDot,
            description="Bring a new specialist onto the team when the roster is missing one the "
            "owner's request needs - write its role card the same way the owner would. The new Dot "
            "starts cautious (approval required, can't delegate or create further Dots) until the "
            "owner adjusts it. Always needs the owner's approval. Delegate to it in a later turn, "
            "once it exists.",
            metadata={"classify": classify_new_dot},
        ),
    ]
    return tools
