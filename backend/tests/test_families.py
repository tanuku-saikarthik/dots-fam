"""Families: the harness team, the graph API, and who may reach whom."""

from __future__ import annotations

from dotsfam.family import reachable

from .test_engine import dot


async def test_install_harness_and_graph(runtime, client):
    vance = dot(runtime, "Vance")
    response = await client.post("/api/team/install-harness", json={})
    assert response.status_code == 201
    again = await client.post("/api/team/install-harness", json={})
    assert again.json()["created"] == []  # idempotent

    graph = (await client.get("/api/graph")).json()
    by_name = {f["name"]: f for f in graph["families"]}
    assert {"office", "harness"} <= set(by_name)
    harness = by_name["harness"]
    names = {n["id"]: n["name"] for n in graph["nodes"]}
    assert [names[i] for i in harness["members"]] == ["Iris", "Bram", "Tess", "Wren"]
    labels = {(names[e["from"]], names[e["to"]], e["kind"]) for e in harness["flow"]}
    assert ("Tess", "Bram", "loop") in labels  # the rework loop is declared structure
    assert ("Tess", "Wren", "pass") in labels

    # Office Dots see only the harness lead; harness workers stay in their own family.
    seen = {d["name"] for d in reachable(runtime.store, vance)}
    assert "Iris" in seen and not {"Bram", "Tess", "Wren"} & seen
    bram = runtime.store.dot_by_name("Bram")
    assert {d["name"] for d in reachable(runtime.store, bram)} == {"Iris", "Tess", "Wren"} | {
        d["name"] for d in runtime.store.dots() if d["can_delegate"] and d["name"] not in {"Iris"}
    } - {"Bram"}
    assert {d["name"] for d in reachable(runtime.store, bram, as_worker=True)} == {"Iris", "Tess", "Wren"}
    # Build mode is set for the dots that code.
    assert runtime.store.dot_by_name("Bram")["local"]["mode"] == "build"
