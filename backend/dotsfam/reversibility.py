"""Classify computer steps for the Reversibility Law.

Browser steps are judged by the element a Dot acts on, using names from its latest
snapshot; shell commands by pattern. It is a classifier, not a sandbox: keep shell
access off unless a Dot needs it.
"""

from __future__ import annotations

import re
from typing import Any

COMMIT_WORDS = re.compile(
    r"\b(send|submit|post|publish|reply|tweet|share|invite|pay|purchase|buy|order|checkout|check out|"
    r"subscribe|unsubscribe|delete|remove|archive|confirm|transfer|withdraw|merge|deploy|approve|book|"
    r"save changes|update|place order|sign up|register)\b",
    re.IGNORECASE,
)
SEARCH_LIKE = re.compile(r"\b(search|find|query|filter|look ?up)\b", re.IGNORECASE)
RISKY_SHELL = re.compile(
    r"\b(git\s+push|gh\s+(pr|issue|release|repo|api)\b(?!\s+(list|view|status|diff|checks))|"
    r"npm\s+publish|yarn\s+publish|pnpm\s+publish|twine\s+upload|docker\s+push|"
    r"kubectl\s+(apply|delete|scale|rollout)|terraform\s+(apply|destroy)|sendmail|mailx?\s|ssh\s|scp\s|"
    r"rsync\s.*:|aws\s+\S+\s+(create|delete|put|update|terminate)|curl\b[^|]*\s-X\s*(POST|PUT|PATCH|DELETE)|"
    r"curl\b[^|]*\s(-d|--data[\w-]*|-F|--form)\b|wget\b[^|]*--post|http(ie)?\s+(POST|PUT|PATCH|DELETE)|"
    r"rm\s+-[a-z]*r[a-z]*\s+/)",
    re.IGNORECASE,
)
ENTER = re.compile(r"(^|\+)(enter|return|numpadenter)$", re.IGNORECASE)


class Snapshots:
    """Latest element names per Dot, so a click can be judged by what it targets."""

    def __init__(self) -> None:
        self._latest: dict[str, tuple[int, dict[str, dict[str, Any]]]] = {}
        self._typed: dict[str, dict[str, Any]] = {}

    def remember(self, dot_id: str, snapshot: dict[str, Any]) -> None:
        elements = {item["ref"]: item for item in snapshot.get("elements", []) if "ref" in item}
        self._latest[dot_id] = (int(snapshot.get("snapshot_id", 0)), elements)

    def element(self, dot_id: str, ref: str, snapshot_id: int) -> dict[str, Any]:
        latest = self._latest.get(dot_id)
        if not latest or latest[0] != snapshot_id or ref not in latest[1]:
            raise ValueError("Take a fresh computer_snapshot and use refs from it.")
        return latest[1][ref]

    def typed(self, dot_id: str, element: dict[str, Any]) -> None:
        self._typed[dot_id] = element

    def last_typed(self, dot_id: str) -> dict[str, Any] | None:
        return self._typed.get(dot_id)


def _describe(element: dict[str, Any]) -> str:
    return f"{element.get('role') or 'element'} “{str(element.get('name', ''))[:80]}”"


def _search(element: dict[str, Any] | None) -> bool:
    return bool(element) and (
        element.get("role") == "searchbox" or bool(SEARCH_LIKE.search(str(element.get("name", ""))))
    )


def classify(snapshots: Snapshots, dot_id: str, action: str, args: dict[str, Any]) -> str | None:
    """A phrase like 'clicks button “Send”' when the step changes the outside world."""
    if action == "click":
        element = snapshots.element(dot_id, args.get("ref", ""), int(args.get("snapshot_id", -1)))
        return (
            f"clicks {_describe(element)}"
            if COMMIT_WORDS.search(str(element.get("name", "")))
            else None
        )
    if action == "type":
        element = snapshots.element(dot_id, args.get("ref", ""), int(args.get("snapshot_id", -1)))
        snapshots.typed(dot_id, element)
        if not args.get("submit") or _search(element):
            return None
        return f"submits {_describe(element)}"
    if action == "press":
        if not ENTER.search(str(args.get("key", ""))):
            return None
        element = snapshots.last_typed(dot_id)
        if _search(element):
            return None
        return f"presses Enter in {_describe(element)}" if element else "presses Enter"
    if action == "shell":
        return (
            "runs a command that changes an outside system"
            if RISKY_SHELL.search(str(args.get("command", "")))
            else None
        )
    return None
