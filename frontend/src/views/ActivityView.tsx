import { useState } from 'react';
import { Square } from 'lucide-react';
import { api, relative, type Activity, type AppState, type Delegation, type Dot } from '../api';
import { ApprovalCard } from '../components/ApprovalCard';
import { DotMark } from '../components/DotMark';
import { Markdown } from './ChatView';

const STATUS_TEXT: Record<string, string> = {
  running: 'Working',
  waiting_approval: 'Waiting for your approval',
  completed: 'Delivered',
  failed: 'Failed',
  cancelled: 'Stopped',
};

function duration(item: Delegation) {
  const ms = (item.finished_at ?? Date.now()) - item.created_at;
  return ms < 60_000 ? `${Math.max(1, Math.round(ms / 1000))} s` : `${Math.round(ms / 60_000)} min`;
}

function Handoffs({ activity, dots, reload }: { activity: Activity; dots: Dot[]; reload: () => void }) {
  const byId = new Map(dots.map((d) => [d.id, d]));
  const groups = new Map<string, Delegation[]>();
  for (const item of [...activity.delegations].reverse())
    groups.set(item.group_id, [...(groups.get(item.group_id) ?? []), item]);
  const ordered = [...groups.values()].reverse();
  if (!ordered.length)
    return (
      <div className="empty">
        <h2>No handoffs yet</h2>
        <p>Ask Vance for something bigger than one job. Every brief he hands a specialist shows up here, step by step.</p>
      </div>
    );
  return (
    <div className="stack">
      {ordered.map((items) => {
        const chief = byId.get(items[0].from_dot_id);
        return (
          <section className="handoff-group" key={items[0].group_id} aria-label="Handoff">
            <div className="row">
              <DotMark dot={chief} />
              <h3 className="grow">
                {chief?.name ?? 'Chief'} handed off {items.length === 1 ? 'one brief' : `${items.length} briefs`}
              </h3>
              <span className="muted small">{relative(items[0].created_at)}</span>
            </div>
            <div className="handoff-tree">
              {items.map((item) => {
                const worker = byId.get(item.to_dot_id);
                const events = activity.delegation_events.filter((e) => e.thread_id === item.worker_thread_id);
                return (
                  <details className="handoff-item" key={item.id}>
                    <summary>
                      <DotMark dot={worker} size="s" />
                      <strong className="grow">{worker?.name ?? 'Specialist'}</strong>
                      <span className={`status ${item.status}`}>{STATUS_TEXT[item.status] ?? item.status}</span>
                      <span className="muted small">{duration(item)}</span>
                    </summary>
                    <div className="body">
                      <div>
                        <h3>Brief</h3>
                        <p className="prose">{item.brief}</p>
                        {item.expected_output && <p className="muted small">Expected: {item.expected_output}</p>}
                      </div>
                      {!!events.length && (
                        <div>
                          <h3>Steps</h3>
                          <div className="timeline">
                            {events.map((event) => (
                              <span key={event.id}>
                                <time>{new Date(event.created_at).toLocaleTimeString()}</time>
                                {event.text}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      {(item.result || item.error) && (
                        <div>
                          <h3>{item.result ? 'Deliverable' : 'What went wrong'}</h3>
                          {item.result ? <Markdown text={item.result} /> : <p className="error">{item.error}</p>}
                        </div>
                      )}
                      <p className="muted small">Model: {item.model ?? 'default'}</p>
                      {(item.status === 'running' || item.status === 'waiting_approval') && (
                        <button
                          className="button"
                          onClick={() => void api(`/delegations/${item.id}/cancel`, 'POST').then(reload)}
                        >
                          <Square size={14} /> Stop this work
                        </button>
                      )}
                    </div>
                  </details>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

export function ActivityView({
  state,
  activity,
  reload,
}: {
  state: AppState;
  activity?: Activity;
  reload: () => void;
}) {
  const [tab, setTab] = useState<'approvals' | 'handoffs'>(state.pending_approvals ? 'approvals' : 'handoffs');
  const pending = activity?.approvals.filter((a) => a.status === 'pending') ?? [];
  const decided = activity?.approvals.filter((a) => a.status !== 'pending').slice(0, 20) ?? [];
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Activity</h1>
          <p className="muted">What your team did while you were away, and what is waiting for you.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'approvals'} onClick={() => setTab('approvals')}>
          Approvals{pending.length ? ` (${pending.length})` : ''}
        </button>
        <button role="tab" aria-selected={tab === 'handoffs'} onClick={() => setTab('handoffs')}>
          Handoffs
        </button>
      </div>
      {!activity ? (
        <p className="muted">Loading…</p>
      ) : tab === 'approvals' ? (
        <div className="stack">
          {pending.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              dot={state.dots.find((d) => d.id === approval.dot_id)}
              onDecided={reload}
            />
          ))}
          {!pending.length && (
            <div className="empty">
              <h2>Nothing is waiting for you</h2>
              <p>
                Your Dots read, research and draft on their own. Anything that sends, posts, pays or deletes stops here
                until you say yes.
              </p>
            </div>
          )}
          {!!decided.length && (
            <>
              <h2>Recently decided</h2>
              {decided.map((approval) => (
                <ApprovalCard
                  key={approval.id}
                  approval={approval}
                  dot={state.dots.find((d) => d.id === approval.dot_id)}
                  onDecided={reload}
                />
              ))}
            </>
          )}
        </div>
      ) : (
        <Handoffs activity={activity} dots={state.dots} reload={reload} />
      )}
    </div>
  );
}
