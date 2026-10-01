"""Classify computer steps for the Reversibility Law.

Browser steps are judged by the element a Dot acts on, using names from its latest
snapshot; key presses by what they can trigger; shell commands by pattern. When the
classifier cannot tell, it asks the owner. It is a guardrail, not a sandbox: keep
shell access off unless a Dot needs it.

The computer service double-checks at action time: clicks and typing verify the
element still matches the snapshot, and key presses that were let through because
focus was in a search or text field verify that focus is really there.
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
NAVIGATION_KEYS = {
    "tab", "shift+tab", "escape", "arrowup", "arrowdown", "arrowleft", "arrowright",
    "pageup", "pagedown", "home", "end",
}  # fmt: skip
TEXT_ROLES = {"textbox", "searchbox", "combobox"}


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("snapshot_id must be a whole number from your latest snapshot.")
    return value


class Snapshots:
    """Per Dot: the latest snapshot's elements, and which element probably has focus."""

    def __init__(self) -> None:
        self._latest: dict[str, tuple[int, dict[str, dict[str, Any]]]] = {}
        self._focus: dict[str, dict[str, Any] | None] = {}

    def remember(self, dot_id: str, snapshot: dict[str, Any]) -> None:
        elements = {item["ref"]: item for item in snapshot.get("elements", []) if "ref" in item}
        self._latest[dot_id] = (int(snapshot.get("snapshot_id", 0)), elements)
        self._focus[dot_id] = elements.get(snapshot.get("focused") or "")

    def element(self, dot_id: str, ref: Any, snapshot_id: Any) -> dict[str, Any]:
        latest = self._latest.get(dot_id)
        if not latest or latest[0] != _int(snapshot_id) or ref not in latest[1]:
            raise ValueError("Take a fresh computer_snapshot and use refs from it.")
        return latest[1][ref]

    def focus(self, dot_id: str, element: dict[str, Any] | None) -> None:
        self._focus[dot_id] = element

    def focused(self, dot_id: str) -> dict[str, Any] | None:
        return self._focus.get(dot_id)


def _describe(element: dict[str, Any]) -> str:
    return f"{element.get('role') or 'element'} “{str(element.get('name', ''))[:80]}”"


def is_search(element: dict[str, Any] | None) -> bool:
    return bool(element) and (
        element.get("role") == "searchbox" or bool(SEARCH_LIKE.search(str(element.get("name", ""))))
    )


def is_text(element: dict[str, Any] | None) -> bool:
    return bool(element) and element.get("role") in TEXT_ROLES


def press_requirement(key: Any) -> str:
    """'free' (navigation), 'search' (Enter: free only in a search box),
    'text' (typing keys: free only in a text field) or 'ask' (shortcuts and unknown keys)."""
    raw = str(key)
    lowered = raw.strip().lower()
    if raw in ("\n", "\r", "\r\n") or re.fullmatch(
        r"(shift\+)?(enter|return|numpadenter)", lowered
    ):
        return "search"
    if lowered in NAVIGATION_KEYS:
        return "free"
    if (
        raw == " "
        or lowered in ("space", "backspace", "delete")
        or (len(raw) == 1 and raw.isprintable())
    ):
        return "text"
    return "ask"  # modifier chords (Ctrl+Enter, Meta+S…), function keys, anything unusual


def classify(snapshots: Snapshots, dot_id: str, action: str, args: dict[str, Any]) -> str | None:
    """A phrase like 'clicks button “Send”' when the step may change the outside world.

    Raises ValueError when the step refers to elements it cannot check; callers treat
    that as "ask the owner".
    """
    if action == "click":
        element = snapshots.element(dot_id, args.get("ref"), args.get("snapshot_id"))
        return (
            f"clicks {_describe(element)}"
            if COMMIT_WORDS.search(str(element.get("name", "")))
            else None
        )
    if action == "type":
        element = snapshots.element(dot_id, args.get("ref"), args.get("snapshot_id"))
        if args.get("submit") is not True or is_search(element):
            return None
        return f"submits {_describe(element)}"
    if action == "press":
        need = press_requirement(args.get("key", ""))
        focused = snapshots.focused(dot_id)
        where = f" in {_describe(focused)}" if focused else ""
        if (
            need == "free"
            or (need == "search" and is_search(focused))
            or (need == "text" and is_text(focused))
        ):
            return None
        return f"presses {str(args.get('key', ''))!r}{where}"
    if action == "shell":
        return (
            "runs a command that changes an outside system"
            if RISKY_SHELL.search(str(args.get("command", "")))
            else None
        )
    raise ValueError(f"Unknown computer action {action}.")
