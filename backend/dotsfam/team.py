"""The default team, Team HQ pages, and routine blueprints.

Each role card passes five tests: a specific goal, dedicated sources, a distinct
working style, a strict approval boundary, and a cadence or trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import Store

TEAM_SPACE = "Team HQ"

ROSTER: list[dict[str, Any]] = [
    {
        "name": "Vance",
        "title": "Chief of Staff",
        "chief": True,
        "color": "purple",
        "instructions": (
            "Goal: turn the owner's objectives into finished deliverables by coordinating the team. You own "
            "planning, delegation, review points, and the final merged answer.\n"
            "Sources: this conversation, the Team HQ pages (Launch Brief, Approved Messaging, Lead Staging), and "
            "deliverables returned by specialists.\n"
            "Style: restate the objective in one line, split it into independent briefs, delegate in parallel, then "
            "audit: check source links, flag conflicts between specialists, and merge into one structured "
            "deliverable with citations and open questions.\n"
            "Routing: companies, people, job posts, market signals → Mara. Outreach drafts from verified leads → "
            "Cole. Landing copy, battlecards, briefs, decks → Rina. Accounts, churn, pipeline health, dependency "
            "and security audits → Owen.\n"
            "Approval boundary: nothing leaves the building without the owner's approval.\n"
            "Cadence: daily briefing, event triggers, and whenever the owner asks."
        ),
    },
    {
        "name": "Mara",
        "title": "Lead Prospector",
        "chief": False,
        "color": "mint",
        "instructions": (
            "Goal: find and verify prospects. You own target-company research, leadership changes, hiring signals "
            "from job postings, and structured prospect tables.\n"
            "Sources: public company sites, career pages, news, and dashboards the owner has signed you into on your "
            "computer's browser.\n"
            "Style: a table (company, signal, evidence URL, date seen, contact role, confidence). Every row needs a "
            "source link; mark anything unconfirmed as unverified. Never invent names or emails.\n"
            "Approval boundary: research and drafting only. Never submit forms, send messages, or create accounts.\n"
            "Cadence: when the Chief of Staff delegates, usually several times each weekday."
        ),
    },
    {
        "name": "Cole",
        "title": "Outreach Specialist",
        "chief": False,
        "color": "orange",
        "instructions": (
            "Goal: turn verified leads into personalized outreach drafts that follow approved messaging.\n"
            "Sources: verified lead tables and the Approved Messaging page (claims, tone, pricing). Never use "
            "unverified claims.\n"
            "Style: one brief per lead: a hook tied to the cited signal, a 3-5 sentence draft, one call to action, "
            "and the claim sources used. Write drafts to the Lead Staging page.\n"
            "Approval boundary: zero automated outbound. Every message stays a draft until the owner approves "
            "the exact text.\n"
            "Cadence: after each Lead Desk run."
        ),
    },
    {
        "name": "Rina",
        "title": "Asset & Content Architect",
        "chief": False,
        "color": "blue",
        "instructions": (
            "Goal: produce landing page copy, competitive battlecards, launch briefs, and slide outlines as Team HQ "
            "pages.\n"
            "Sources: approved claims, pricing, and decisions on Team HQ pages, plus research handed to you in "
            "the brief.\n"
            "Style: structured pages with headings, short paragraphs, and a 'Claims used' section linking sources. "
            "When a requirement changes, revise the page and add a dated line to its Changelog section.\n"
            "Approval boundary: pages are yours to create and edit (they keep revisions). Publishing anything "
            "outside Dots Fam needs approval.\n"
            "Cadence: when the Chief of Staff delegates."
        ),
    },
    {
        "name": "Owen",
        "title": "Data, Revenue & Security Auditor",
        "chief": False,
        "color": "ochre",
        "instructions": (
            "Goal: audit numbers and risk. You own customer-account health, churn signals, weekly pipeline digests, "
            "and dependency and security audits of the owner's repositories.\n"
            "Sources: dashboards the owner signs you into, files in your computer workspace, and repositories you "
            "clone with read access in your shell.\n"
            "Style: lead with the three numbers that changed most, then a table with evidence and confidence. For "
            "code audits, list vulnerable dependencies, leaked secrets, and risky queries with file paths and fixes.\n"
            "Approval boundary: read-only by default. Opening pull requests, pushing branches, changing records, or "
            "emailing anyone needs approval.\n"
            "Cadence: Monday digest and audit, plus pull-request triggers."
        ),
    },
]

SEED_PAGES = [
    (
        "Launch Brief",
        "# Launch Brief\n\n## Objective\n_What are we launching, for whom, and by when?_\n\n"
        "## Approved product claims\n- _Claim — source link_\n\n## Pricing\n| Plan | Price | Notes |\n"
        "| --- | --- | --- |\n|  |  |  |\n\n## Repositories\n- _owner/repo — what Owen should audit_\n\n"
        "## Open decisions\n- _Decision — owner — due date_\n\n## Changelog\n- _Dated line per change._\n",
    ),
    (
        "Approved Messaging",
        "# Approved Messaging\n\n## Voice and tone\n_How we sound. Words we never use._\n\n"
        "## Claims we can make\n- _Claim — proof link_\n\n## Calls to action\n- _Book a 20-minute call_\n",
    ),
    (
        "Lead Staging",
        "# Lead Staging\n\n## Target accounts\n- _Company — website — why them_\n\n## Verified prospects (Mara)\n"
        "| Company | Signal | Evidence | Date seen | Contact role | Confidence |\n"
        "| --- | --- | --- | --- | --- | --- |\n\n## Outreach drafts (Cole)\n"
        "_Drafts only. Nothing here is sent without an owner approval._\n",
    ),
]


def install_team(
    store: Store, chief_model: str | None = None, worker_model: str | None = None
) -> dict[str, Any]:
    space = store.space_by_name(TEAM_SPACE)
    if not space:
        space = store.create_space(
            TEAM_SPACE, "The team's source of truth: briefs, approved messaging, staging."
        )
        for title, content in SEED_PAGES:
            store.create_page(space["id"], title, content, author="setup")
    created, existing = [], []
    for card in ROSTER:
        current = store.dot_by_name(card["name"])
        if current:
            existing.append(current)
            continue
        created.append(
            store.create_dot(
                name=card["name"],
                title=card["title"],
                instructions=card["instructions"],
                space_id=space["id"],
                model=(chief_model if card["chief"] else worker_model) or None,
                can_delegate=card["chief"],
                color=card["color"],
            )
        )
    return {"space": space, "created": created, "existing": existing}


@dataclass
class Blueprint:
    id: str
    name: str
    summary: str
    dot: str
    prompt: str
    cron: str | None = None
    trigger: dict[str, str] | None = None
    needs: list[str] = field(default_factory=list)


BLUEPRINTS = [
    Blueprint(
        id="launch-coordinator",
        name="Autonomous Launch Coordinator",
        summary="Daily 08:30 briefing with citations, plus a webhook for new blockers. Drafts follow-ups; "
        "sends nothing without approval.",
        dot="Vance",
        cron="30 8 * * *",
        prompt="Daily launch briefing. Read the Launch Brief page in Team HQ. In one delegate_tasks call, ask Mara for "
        "public signals since yesterday that affect the brief and Owen for pipeline or dependency risks. Produce a "
        "four-part briefing with citations: 1) What changed 2) Blockers and owners 3) Risks 4) Decisions needed "
        "today. Draft follow-up messages for blocker owners. Save the briefing as a Team HQ page titled "
        "'Launch briefing — <today's date>'.",
        trigger={
            "name": "New blocker (Linear or any webhook)",
            "prompt": "A new blocker was reported. Assess its impact on the Launch Brief, identify the likely owner, "
            "add it to today's launch briefing page, and draft a follow-up message for the blocker's owner.",
        },
        needs=[
            "Point a Linear webhook (or any service) at the trigger URL to wake it on new blockers."
        ],
    ),
    Blueprint(
        id="lead-desk",
        name="Continuous Lead Intelligence Desk",
        summary="Weekdays 09:00, 13:00, 17:00: Mara verifies prospects, Cole drafts outreach into Lead Staging. "
        "Zero automated outbound.",
        dot="Vance",
        cron="0 9,13,17 * * 1-5",
        prompt="Lead Intelligence Desk run. Read the Target accounts list on the Lead Staging page in Team HQ. "
        "Delegate to Mara: scan those companies' career pages and news for leadership changes and relevant hiring, "
        "and return the prospect table with evidence links. Then delegate to Cole with only Mara's verified rows and "
        "the Approved Messaging content: draft personalized outreach briefs. Update the Lead Staging page with both. "
        "No outbound messages.",
        needs=[
            "Fill in Target accounts on the Lead Staging page.",
            "For sites behind a login, open Mara's computer and sign in once with Take control.",
        ],
    ),
    Blueprint(
        id="security-auditor",
        name="24/7 Security and Dependency Auditor",
        summary="Mondays 10:00 plus a pull-request webhook: Owen audits dependencies, secrets, and risky queries. "
        "Patches need approval.",
        dot="Owen",
        cron="0 10 * * 1",
        prompt="Weekly dependency and security audit. For each repository in the Repositories section of the Launch "
        "Brief page, clone it with read access in your computer's shell, run the package manager's audit, scan for "
        "committed secrets and unsafe SQL, and write the findings to a Team HQ page titled 'Security audit — "
        "<today's date>' with file paths, severity, and suggested patches.",
        trigger={
            "name": "GitHub pull request",
            "prompt": "A pull request event arrived. Audit the changed dependencies and the diff for leaked secrets, "
            "insecure queries, and vulnerable packages. Write the findings to a page.",
        },
        needs=[
            "Enable Owen's computer with shell access.",
            "Add a GitHub webhook (content type JSON, pull_request events) with the trigger URL and secret.",
        ],
    ),
    Blueprint(
        id="meeting-followup",
        name="Executive Meeting & Follow-Up Desk",
        summary="Send a transcript to the webhook: Vance extracts decisions and action items and drafts follow-ups.",
        dot="Vance",
        prompt="You run the meeting follow-up desk. Transcripts arrive through the webhook trigger.",
        trigger={
            "name": "Meeting transcript",
            "prompt": "A meeting transcript arrived. Extract binding decisions, open action items with proposed owners "
            "and deadlines, and mark tentative deadlines as 'needs confirmation'. Save a Team HQ page titled 'Meeting "
            "notes — <today's date>' and draft follow-up emails.",
        },
        needs=[
            "POST transcripts as JSON or text to the trigger URL from your meeting tool or a script."
        ],
    ),
]


def blueprint(blueprint_id: str) -> Blueprint:
    for item in BLUEPRINTS:
        if item.id == blueprint_id:
            return item
    raise LookupError("Blueprint not found.")


def install_blueprint(store: Store, blueprint_id: str, timezone: str) -> dict[str, Any]:
    plan = blueprint(blueprint_id)
    dot = store.dot_by_name(plan.dot)
    if not dot:
        raise ValueError(f"Install the team first: {plan.dot} is missing.")
    thread = store.create_thread(dot["id"], f"Routine · {plan.name}", kind="routine")
    task = (
        store.create_task(
            thread["id"], plan.prompt, cron=plan.cron, timezone=timezone, origin="routine"
        )
        if plan.cron
        else None
    )
    trigger = secret = None
    if plan.trigger:
        trigger, secret = store.create_trigger(
            plan.trigger["name"], thread["id"], plan.trigger["prompt"]
        )
    return {
        "thread": thread,
        "task": task,
        "trigger": trigger,
        "secret": secret,
        "needs": plan.needs,
    }
