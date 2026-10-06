# Agent Office

A 3D isometric visualization of the Dots Fam team as an office: each worker Dot
sits at a desk, a Coordinator desk assigns tasks, and handoffs between Dots are
shown as a stand-up → walk-with-glowing-folder → hand-off → walk-back animation.

It's reachable at `#/office` in the app. Everything is driven by a small,
typed event stream — the 3D scene itself never knows whether that stream comes
from the built-in mock simulator or a real backend.

## Event schema

```ts
type OfficeEvent =
  | { type: 'task_assigned'; taskId: string; title: string; to: AgentId }
  | { type: 'task_started'; taskId: string; agent: AgentId }
  | { type: 'task_progress'; taskId: string; agent: AgentId; progress: number } // 0..1
  | { type: 'handoff'; taskId: string; from: AgentId; to: AgentId; note?: string }
  | { type: 'task_completed'; taskId: string; agent: AgentId }
  | { type: 'agent_waiting'; agent: AgentId; reason?: string };

type AgentId = 'coordinator' | 'researcher' | 'planner' | 'coder' | 'reviewer' | 'tester' | 'writer';
```

`AgentId` is currently a fixed 6-worker-plus-coordinator roster
(`src/office/events/types.ts`, `src/office/nav/layout.ts`). To drive this from
the real Dots Fam team instead of the generic office roles, map each of your
Dots to one of these ids (or widen the type and add a desk per Dot in
`nav/layout.ts` — the grid, A*, and desk rendering are all data-driven from
that one file).

## Pointing it at a real backend

By default `OfficeView` connects with `connect('mock')`, which plays scripted
flows from `events/MockSimulator.ts` on a loop. To connect to a live backend
instead:

```ts
connect('ws', 'ws://127.0.0.1:8787/office/stream');
```

The server just needs to push newline-delimited JSON frames matching
`OfficeEvent` over that WebSocket, e.g. whenever Vance delegates work to Mara
in the real chat backend, emit:

```json
{"type":"handoff","taskId":"thread-123","from":"coordinator","to":"researcher","note":"Check provider options"}
```

`WebSocketSource` auto-reconnects (2s backoff) and reports connection status
via `onStatusChange`, shown in the top bar.

## Known simplifications (v1)

- No agent-vs-agent collision avoidance — only agents-vs-desks (A* treats
  desks as the only obstacles). Fine at this roster size; revisit if the
  office gets much busier.
- No target-busy mutex: if a `handoff` arrives for a receiver that's already
  mid-task, the new clips are simply queued after their current one rather
  than making the giver visibly wait at the standpoint. The `agent_waiting`
  event still drives the amber status light for self-reported waits (the
  pattern the mock simulator uses).
- Respects `prefers-reduced-motion` by shortening clip durations and walk
  time (see `agents/stateMachine.ts` / `agents/animations.ts`), not by fully
  disabling motion.
