"""Families: teams of Dots that work together, each with its own structure.

The office family is a hub (a Chief of Staff and specialists). The build harness is a
pipeline (plan, build, verify, ship) with a rework loop. The Chief can also start small
subteams on demand. A Dot hands work to its own family; a lead can also hand a whole job
to another family's lead.
"""

from __future__ import annotations

from typing import Any

from .db import Store

OFFICE = "office"


def lead_of(store: Store, family: str) -> dict[str, Any] | None:
    members = store.family_members(family)
    return next((d for d in members if d["can_delegate"]), None)


def reachable(store: Store, from_dot: dict[str, Any], *, as_worker: bool = False) -> list[dict[str, Any]]:
    """Who this Dot may hand work to: its own family, plus other families' leads.

    A lead that is itself doing delegated work (a worker) stays inside its own family,
    so jobs can't bounce between families.
    """
    mine = from_dot.get("family") or OFFICE
    out = []
    for dot in store.dots():
        if dot["id"] == from_dot["id"]:
            continue
        if (dot.get("family") or OFFICE).lower() == mine.lower():
            out.append(dot)
        elif not as_worker and dot["can_delegate"]:
            out.append(dot)
    return out


def flow_edges(store: Store, family: dict[str, Any] | None, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The declared structure: the pipeline for a harness, lead to everyone for a hub."""
    ids = {d["id"] for d in members}
    if family and family.get("flow"):
        return [e for e in family["flow"] if e["from"] in ids and e["to"] in ids]
    lead = next((d for d in members if d["can_delegate"]), None)
    if lead is None:
        return []
    return [
        {"from": lead["id"], "to": d["id"], "label": "", "kind": "flow"}
        for d in members
        if d["id"] != lead["id"]
    ]
