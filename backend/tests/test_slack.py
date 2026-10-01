"""Slack bridge with a fake Slack client: threads, routing, approvals by button, scheduled results."""

from __future__ import annotations

import asyncio

import pytest

from dotsfam.slack import APPROVE, SlackBridge, to_mrkdwn

from .conftest import call, finish
from .test_engine import FakeOutbox


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.reactions: list[tuple[str, str]] = []
        self.counter = 100

    async def chat_postMessage(self, **kwargs):  # noqa: N802 - Slack SDK name
        self.counter += 1
        self.posts.append(kwargs)
        return {"ok": True, "ts": f"{self.counter}.0"}

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}

    async def reactions_add(self, **kwargs):
        self.reactions.append((kwargs["channel"], kwargs["name"]))
        return {"ok": True}

    async def conversations_list(self, **_kwargs):
        return {"channels": [{"id": "C0LAUNCH01", "name": "launch"}], "response_metadata": {}}

    async def conversations_history(self, **_kwargs):
        return {"messages": [{"user": "U2", "ts": "1.0", "text": "Ship it Friday?"}]}


@pytest.fixture
def slack(runtime, settings):
    settings.slack_allowed_users = "UOWNER"
    fake = FakeSlack()
    bridge = SlackBridge(runtime, client=fake)
    runtime.attach_slack(bridge)
    return bridge, fake


async def settle(runtime, seconds: float = 10) -> None:
    """Wait until no runs or queued Slack follow-ups remain."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        await runtime.scheduler.tick()
        if not runtime.runs.active_runs() and not any(
            t["status"] in ("queued", "running") for t in runtime.store.tasks()
        ):
            await asyncio.sleep(0.05)
            if not runtime.runs.active_runs():
                return
        await asyncio.sleep(0.05)
    raise AssertionError("runs did not settle")


def mention(text: str, ts: str = "1.0", user: str = "UOWNER", **extra) -> dict:
    return {"channel": "C0GENERAL1", "ts": ts, "user": user, "text": f"<@UBOT> {text}", **extra}


def test_markdown_becomes_slack_mrkdwn():
    assert to_mrkdwn("**Hi** see [docs](https://x.example)\n# Plan\n- one") == (
        "*Hi* see <https://x.example|docs>\n*Plan*\n• one"
    )


async def test_mention_starts_a_conversation_with_the_chief_and_replies_in_thread(
    runtime, script, slack
):
    bridge, fake = slack
    script.add("Vance", "Two launches need you today.")
    await bridge.handle_message(mention("what needs me today?"))
    await settle(runtime)
    vance = runtime.store.dot_by_name("Vance")
    [thread] = runtime.store.threads(vance["id"])
    assert thread["title"] == "what needs me today?"
    assert fake.posts[-1] == {
        "channel": "C0GENERAL1",
        "thread_ts": "1.0",
        "text": "Two launches need you today.",
        "unfurl_links": False,
    }
    history = await runtime.runs.history(thread["id"])
    assert history[0]["source"] == "slack"
    # A reply in the same Slack thread continues the same conversation.
    script.add("Vance", "Done.")
    await bridge.handle_message(
        {
            "channel": "C0GENERAL1",
            "ts": "2.0",
            "thread_ts": "1.0",
            "user": "UOWNER",
            "text": "thanks",
        }
    )
    await settle(runtime)
    assert len(runtime.store.threads(vance["id"])) == 1
    assert fake.posts[-1]["text"] == "Done."


async def test_name_prefix_routes_to_a_specialist(runtime, script, slack):
    bridge, fake = slack
    script.add("Mara", "Found 3 leads.")
    await bridge.handle_message(mention("Mara: find leads in Pune"))
    await settle(runtime)
    mara = runtime.store.dot_by_name("Mara")
    [thread] = runtime.store.threads(mara["id"])
    history = await runtime.runs.history(thread["id"])
    assert history[0]["text"] == "find leads in Pune"
    assert fake.posts[-1]["text"] == "Found 3 leads."


async def test_strangers_are_turned_away(runtime, slack):
    bridge, fake = slack
    await bridge.handle_message(mention("delete everything", user="USTRANGER"))
    assert runtime.store.threads() == []
    assert "only take requests from the owner" in fake.posts[-1]["text"]


async def test_specialist_approval_arrives_as_buttons_in_the_owners_thread(runtime, script, slack):
    bridge, fake = slack
    outbox = FakeOutbox()
    runtime.runs.extra["outbox"] = outbox
    script.add(
        "Vance",
        call(
            "delegate_tasks",
            {"assignments": [{"dot": "Cole", "brief": "Email Ana the intro."}], "wait_seconds": 5},
        ),
        "Cole drafted the intro; it is waiting for your approval.",
        "Cole sent the intro to Ana.",
    )
    script.add("Cole", call("send_email", {"to": "ana@acme.example", "body": "Hi Ana"}), "Sent.")
    await bridge.handle_message(mention("get an intro to Ana going"))
    await settle(runtime)
    [card] = [p for p in fake.posts if "blocks" in p]
    assert card["thread_ts"] == "1.0"
    approve = card["blocks"][-1]["elements"][0]
    assert approve["action_id"] == APPROVE
    assert outbox.executed == []
    await bridge.handle_action(
        "UOWNER", approve["value"], "approved", channel="C0GENERAL1", ts="9.0"
    )
    await settle(runtime)
    assert outbox.executed == [("ana@acme.example", "Hi Ana")]
    assert fake.updates[-1]["text"] == "Approved by <@UOWNER>"
    # The Chief's follow-up with the delivered work lands in the same Slack thread.
    assert fake.posts[-1]["text"] == "Cole sent the intro to Ana."
    assert fake.posts[-1]["thread_ts"] == "1.0"


async def test_only_the_owner_can_press_approve(runtime, script, slack):
    bridge, fake = slack
    outbox = FakeOutbox()
    runtime.runs.extra["outbox"] = outbox
    script.add("Cole", call("send_email", {"to": "a@b.example", "body": "x"}), "Sent.")
    await bridge.handle_message(mention("Cole: email a@b.example"))
    await settle(runtime)
    [approval] = [a for a in runtime.store.approvals() if a["status"] == "pending"]
    await bridge.handle_action(
        "USTRANGER", approval["id"], "approved", channel="C0GENERAL1", ts="9"
    )
    assert runtime.store.approval(approval["id"])["status"] == "pending"
    assert outbox.executed == []


async def test_dots_can_read_slack_but_posting_waits_for_approval(runtime, script, slack):
    bridge, fake = slack
    rina = runtime.store.dot_by_name("Rina")
    thread = runtime.store.create_thread(rina["id"], "Launch")
    script.add(
        "Rina",
        call("slack_read", {"channel": "#launch"}, "r1"),
        call("slack_post", {"channel": "#launch", "text": "**Launch** moved to Friday"}, "r2"),
        "Posted.",
    )
    await runtime.runs.send(thread["id"], "Tell #launch about Friday")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "waiting_approval"
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    assert approval["tool"] == "slack_post" and approval["reason"] == "posts a message to Slack"
    assert not any(p["channel"] == "C0LAUNCH01" for p in fake.posts)
    await runtime.decide(approval["id"], "approved")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"
    [post] = [p for p in fake.posts if p["channel"] == "C0LAUNCH01"]
    assert post["text"] == "*Launch* moved to Friday"


async def test_routine_results_go_to_the_notify_channel(runtime, script, slack, settings):
    bridge, fake = slack
    settings.slack_notify_channel = "C0BRIEFS01"
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Daily brief")
    runtime.store.create_task(thread["id"], "Write the daily brief.", origin="routine")
    script.add("Vance", "Brief: all green.")
    await settle(runtime)
    assert fake.posts[-1]["channel"] == "C0BRIEFS01"
    assert fake.posts[-1]["text"] == "*Vance* (Daily brief):\nBrief: all green."
    # Replying in that Slack thread continues the routine's conversation.
    assert runtime.store.slack_thread("C0BRIEFS01", "101.0") == thread["id"]
