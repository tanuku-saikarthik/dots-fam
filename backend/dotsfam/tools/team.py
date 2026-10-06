"""Team tools: delegating to a specialist, splitting your own work in parallel,
and - for the Chief of Staff - bringing a new specialist onto the team."""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..context import DotContext
from ..family import OFFICE, reachable
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


MAX_CREATED_DOTS = 12  # Dots a Chief may create in total
MAX_FAMILY_SIZE = 6
MAX_SUBTEAMS = 4
MAX_PEER_ASKS_PER_THREAD = 6


class Teammate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    title: str = Field("", max_length=60)
    instructions: str = Field(min_length=10, max_length=4000)
    model: str | None = None


class CreateTeam(BaseModel):
    name: str = Field(min_length=2, max_length=30, description="Team name, e.g. 'Mobile research'.")
    summary: str = Field("", max_length=300, description="What this team is for.")
    members: list[Teammate] = Field(
        min_length=2,
        max_length=MAX_FAMILY_SIZE,
        description="2-6 Dots. The first one leads: it receives whole jobs and may hand work to the others.",
    )


class AskPeer(BaseModel):
    peer: str = Field(max_length=80, description="Name of a teammate in YOUR team.")
    question: str = Field(
        min_length=5, max_length=4000, description="Self-contained question or request. The peer sees only this."
    )
    wait_seconds: int = Field(90, ge=5, le=240)


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
    for dot in reachable(ctx.store, ctx.dot, as_worker=ctx.worker is not None):
        mine = (dot.get("family") or "office") == (ctx.dot.get("family") or "office")
        role = " ".join(dot["instructions"].split())[:260]
        title = f" ({dot['title']})" if dot["title"] else ""
        local = dot.get("local") or {}
        if local.get("enabled") and local.get("mode") == "build" and local.get("project_dir"):
            role += (
                f" [Codes in {local['project_dir']}: works on its own branch, runs tests, and opens "
                "a pull request.]"
            )
        where = "" if mine else f" [leads the {dot['family']} team: hand it a whole job]"
        lines.append(f"- {dot['name']}{title}{where}: {role}")
    return "\n".join(lines) or "- (no specialists yet)"


def team_tools(ctx: DotContext) -> list[BaseTool]:
    if not ctx.delegations:
        return []
    # A spawned worker gets no team tools, with one exception: a team lead handed a whole job
    # by another family's lead may delegate inside its own family (so the harness can run its loop).
    lead_worker = bool(
        ctx.worker
        and ctx.dot.get("can_delegate")
        and (ctx.worker.from_dot.get("family") or OFFICE) != (ctx.dot.get("family") or OFFICE)
    )
    if ctx.worker and not lead_worker:
        return []  # no recursive fan-out/delegation
    manager = ctx.delegations
    tools: list[BaseTool] = []
    if lead_worker:
        return _lead_worker_tools(ctx, manager)
    tools.extend(_peer_tools(ctx, manager))

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
            as_worker=ctx.worker is not None,
        )

    def _hub_chief() -> bool:
        return (ctx.dot.get("family") or OFFICE) == OFFICE

    def _check_room(family: str, adding: int) -> None:
        created = [d for d in ctx.store.dots() if d.get("created_by")]
        if len(created) + adding > MAX_CREATED_DOTS:
            raise ValueError(f"The team already has {len(created)} Dots made by the Chief (limit {MAX_CREATED_DOTS}).")
        if len(ctx.store.family_members(family)) + adding > MAX_FAMILY_SIZE:
            raise ValueError(f'The "{family}" team is full (limit {MAX_FAMILY_SIZE}).')

    def _make(spec: dict, family: str, lead: bool) -> dict:
        name = spec["name"].strip()
        if not name:
            raise ValueError("Give the new Dot a name.")
        if ctx.store.dot_by_name(name):
            raise ValueError(f'"{name}" already exists on the team.')
        if spec.get("model"):
            parse_ref(spec["model"])  # clear error for a malformed provider:model string
        used = {d["color"] for d in ctx.store.dots()}
        color = next((c for c in COLORS if c not in used), COLORS[len(ctx.store.dots()) % len(COLORS)])
        # Power cap: a created Dot never gets more than its creator and never gets a computer or
        # local project access. Only a subteam lead may hand work to teammates. Approval stays on.
        return ctx.store.create_dot(
            name=name,
            title=spec.get("title", ""),
            instructions=spec["instructions"],
            space_id=ctx.dot["space_id"],
            space_ids=ctx.dot.get("space_ids"),
            model=spec.get("model"),
            can_delegate=lead,
            approval_mode="reversible",
            research_allowed=True,
            memory_allowed=True,
            color=color,
            family=family,
            created_by=ctx.dot["id"],
        )

    async def create_dot(
        name: str, title: str = "", instructions: str = "", model: str | None = None
    ) -> dict:
        ctx.check()
        if not _hub_chief():
            raise ValueError("Only the Chief of Staff can bring in new Dots.")
        _check_room(OFFICE, 1)
        dot = _make(
            {"name": name, "title": title, "instructions": instructions, "model": model}, OFFICE, False
        )
        return {"id": dot["id"], "name": dot["name"], "title": dot["title"], "color": dot["color"]}

    async def create_team(name: str, summary: str = "", members: list[Teammate] | None = None) -> dict:
        ctx.check()
        if not _hub_chief():
            raise ValueError("Only the Chief of Staff can start a team.")
        key = name.strip()
        if not key or key.lower() in {OFFICE, "harness"} or ctx.store.family(key):
            raise ValueError(f'A team called "{key}" already exists or the name is reserved.')
        subteams = [f for f in ctx.store.families() if f.get("created_by")]
        if len(subteams) >= MAX_SUBTEAMS:
            raise ValueError(f"The limit is {MAX_SUBTEAMS} teams made by the Chief.")
        specs = [m.model_dump() for m in members or []]
        _check_room(key, len(specs))
        if len({m["name"].lower() for m in specs}) != len(specs):
            raise ValueError("Teammates need different names.")
        ctx.store.save_family(key, title=key, summary=summary, kind="hub", created_by=ctx.dot["id"])
        made = [_make(spec, key, i == 0) for i, spec in enumerate(specs)]
        return {
            "team": key,
            "lead": made[0]["name"],
            "members": [d["name"] for d in made],
            "note": "Hand the lead a whole job with delegate_tasks. Teammates can ask each other directly.",
        }

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
            "starts cautious (can't delegate, no computer, risky actions still need the owner's "
            "approval). No approval is needed to create it; the owner sees it appear and can remove it. "
            "Delegate to it in a later turn, once it exists.",
        ),
        StructuredTool.from_function(
            coroutine=create_team,
            name="create_team",
            args_schema=CreateTeam,
            description="Start a subteam of 2-6 new Dots that work on a job together: the first member "
            "leads and takes whole jobs from you, members can ask each other directly. Same power cap "
            "as create_dot. Use for a multi-part job that needs several new specialists.",
        ),
    ]
    return tools


def _peer_tools(ctx: DotContext, manager) -> list[BaseTool]:
    """Dots in the same team may ask each other directly. One hop only: the peer answers, and
    cannot ask anyone else. Capped per conversation."""
    mine = [d for d in reachable(ctx.store, ctx.dot, as_worker=True)]
    if not mine:
        return []

    async def ask_peer(peer: str, question: str, wait_seconds: int = 90) -> dict:
        ctx.check()
        return await manager.ask_peer(
            ctx.dot,
            ctx.thread_id,
            peer,
            question,
            wait_seconds=wait_seconds,
            cancelled=ctx.cancelled,
        )

    names = ", ".join(d["name"] for d in mine)
    return [
        StructuredTool.from_function(
            coroutine=ask_peer,
            name="ask_peer",
            args_schema=AskPeer,
            description="Ask a teammate in your own team a question or for a quick piece of work, and "
            f"wait for the answer. Teammates: {names}. They see only your question and cannot ask anyone else.",
        )
    ]


def _lead_worker_tools(ctx: DotContext, manager) -> list[BaseTool]:
    """A team lead handed a whole job by another team's lead: delegate inside its own team only."""

    async def delegate_tasks(assignments: list[Assignment], wait_seconds: int = 120) -> dict:
        ctx.check()
        return await manager.dispatch(
            ctx.dot,
            ctx.thread_id,
            [item.model_dump() for item in assignments],
            wait_seconds=wait_seconds,
            cancelled=ctx.cancelled,
            as_worker=True,
        )

    return [
        StructuredTool.from_function(
            coroutine=delegate_tasks,
            name="delegate_tasks",
            args_schema=Delegate,
            description="Hand scoped work to the specialists on YOUR team (1-5 assignments). Each sees "
            f"only its brief and returns a deliverable.\nRoster:\n{roster_text(ctx)}",
        )
    ]
