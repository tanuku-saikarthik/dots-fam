import { useCallback, useEffect, useState } from 'react';
import {
  Check,
  ChevronRight,
  Copy,
  LoaderCircle,
  RotateCw,
  ShieldCheck,
  Square,
  Trash2,
  Webhook,
  Workflow,
  X,
} from 'lucide-react';
import type {
  Approval,
  Delegation,
  Dot,
  TeamState,
  Trigger,
  WorkspaceState,
} from '../shared/types';
import { api } from './api';
import { Mascot } from './Mascot';
import { relative } from './TaskPresentation';

export interface TeamResponse extends TeamState {
  pendingApprovals: number;
  hookBase: string;
  roster: { name: string; title: string; chief: boolean }[];
  blueprints: {
    id: string;
    name: string;
    summary: string;
    dot: string;
    cron: string | null;
    needs: string[];
    trigger?: { name: string };
  }[];
  models: string[];
}

export function useTeam(enabled = true) {
  const [team, setTeam] = useState<TeamResponse>();
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      setTeam(await api<TeamResponse>('/team'));
      setError('');
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Could not load team activity.',
      );
    }
  }, []);
  useEffect(() => {
    if (!enabled) return;
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [enabled, load]);
  return { team, error, reload: load };
}

const dotName = (dots: Dot[], id: string) =>
  dots.find((dot) => dot.id === id)?.name ?? 'Removed Dot';
const duration = (item: Delegation) => {
  const ms = (item.finishedAt ?? Date.now()) - item.createdAt;
  return ms < 60_000
    ? `${Math.round(ms / 1000)}s`
    : `${Math.round(ms / 60_000)}m`;
};

function ApprovalCard({
  approval,
  dots,
  onDecide,
}: {
  approval: Approval;
  dots: Dot[];
  onDecide: (decision: 'approved' | 'declined') => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const decide = async (decision: 'approved' | 'declined') => {
    setBusy(true);
    await onDecide(decision);
    setBusy(false);
  };
  return (
    <article className={`team-card approval ${approval.status}`}>
      <header>
        <Mascot
          identity={approval.dotId}
          name={dotName(dots, approval.dotId)}
          small
          decorative
        />
        <div>
          <strong>{approval.summary}</strong>
          <span>
            {dotName(dots, approval.dotId)} ·{' '}
            {approval.kind.replaceAll('_', ' ')} ·{' '}
            {relative(approval.createdAt)}
            {approval.delegationId ? ' · via delegation' : ''}
          </span>
        </div>
        <span className={`status ${approval.status}`}>
          <span />
          {approval.status}
        </span>
      </header>
      {approval.details && (
        <pre className="team-details">{approval.details}</pre>
      )}
      {approval.status === 'pending' && (
        <div className="task-controls">
          <button
            className="primary"
            disabled={busy}
            onClick={() => void decide('approved')}
          >
            <Check size={14} />
            Approve
          </button>
          <button disabled={busy} onClick={() => void decide('declined')}>
            <X size={14} />
            Decline
          </button>
          <small className="muted">
            The decision goes back to the conversation that asked. Approval
            covers this action only, for 2 hours.
          </small>
        </div>
      )}
    </article>
  );
}

function DelegationGroup({
  items,
  team,
  dots,
  onCancel,
}: {
  items: Delegation[];
  team: TeamResponse;
  dots: Dot[];
  onCancel: (id: string) => void;
}) {
  const [open, setOpen] = useState<string>();
  const from = items[0];
  return (
    <article className="team-card">
      <header>
        <Mascot
          identity={from.fromDotId}
          name={dotName(dots, from.fromDotId)}
          small
          decorative
        />
        <div>
          <strong>
            {dotName(dots, from.fromDotId)} delegated to{' '}
            {items.map((item) => dotName(dots, item.toDotId)).join(', ')}
          </strong>
          <span>{relative(from.createdAt)}</span>
        </div>
      </header>
      <ol className="delegation-tree">
        {items.map((item) => {
          const events = team.events.filter(
            (event) => event.delegationId === item.id,
          );
          const expanded = open === item.id;
          return (
            <li key={item.id}>
              <button
                className="delegation-row"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? undefined : item.id)}
              >
                <Mascot
                  identity={item.toDotId}
                  name={dotName(dots, item.toDotId)}
                  small
                  decorative
                />
                <span className="delegation-name">
                  {dotName(dots, item.toDotId)}
                  <small>
                    {item.model ?? 'default model'} · {duration(item)}
                  </small>
                </span>
                <span className={`status ${item.status}`}>
                  {item.status === 'running' ? (
                    <LoaderCircle className="spin" size={10} />
                  ) : (
                    <span />
                  )}
                  {item.status}
                </span>
                <ChevronRight
                  size={15}
                  className={expanded ? 'rotated' : undefined}
                />
              </button>
              {expanded && (
                <div className="delegation-body">
                  <h4>Brief</h4>
                  <pre className="team-details">{item.brief}</pre>
                  {item.expectedOutput && (
                    <>
                      <h4>Expected output</h4>
                      <p>{item.expectedOutput}</p>
                    </>
                  )}
                  <h4>Activity</h4>
                  <ul className="delegation-events">
                    {events.map((event) => (
                      <li key={event.id}>
                        <time>
                          {new Date(event.createdAt).toLocaleTimeString()}
                        </time>
                        {event.text}
                      </li>
                    ))}
                  </ul>
                  {(item.result || item.error) && (
                    <>
                      <h4>{item.result ? 'Deliverable' : 'Error'}</h4>
                      <pre className="team-details">
                        {item.result ?? item.error}
                      </pre>
                    </>
                  )}
                  {item.status === 'running' && (
                    <button
                      className="quiet-button"
                      onClick={() => onCancel(item.id)}
                    >
                      <Square size={12} />
                      Stop this work
                    </button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </article>
  );
}

function TriggerCard({
  trigger,
  hookBase,
  workspace,
  onChange,
  onError,
}: {
  trigger: Trigger;
  hookBase: string;
  workspace: WorkspaceState;
  onChange: () => void;
  onError: (message: string) => void;
}) {
  const [secret, setSecret] = useState<string>();
  const thread = workspace.conversations.find(
    (item) => item.id === trigger.threadId,
  );
  const url = `${hookBase}/${trigger.id}`;
  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      onChange();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not update trigger.');
    }
  };
  return (
    <article className="team-card">
      <header>
        <span className="team-icon">
          <Webhook size={16} />
        </span>
        <div>
          <strong>{trigger.name}</strong>
          <span>
            {thread
              ? `${dotName(workspace.dots, thread.dotId)} · ${thread.title}`
              : 'Conversation unavailable'}{' '}
            · fired {trigger.fireCount}×
            {trigger.lastFiredAt
              ? `, last ${relative(trigger.lastFiredAt)}`
              : ''}
          </span>
        </div>
        <label className="toggle-inline">
          <input
            type="checkbox"
            checked={trigger.enabled}
            onChange={(e) =>
              void run(() =>
                api(`/triggers/${trigger.id}`, 'PATCH', {
                  enabled: e.target.checked,
                }),
              )
            }
          />
          Enabled
        </label>
      </header>
      <div className="hook-url">
        <code>{url}</code>
        <button
          className="icon-button"
          aria-label="Copy webhook URL"
          onClick={() => void navigator.clipboard?.writeText(url)}
        >
          <Copy size={14} />
        </button>
      </div>
      {secret && (
        <div className="hook-url secret">
          <code>{secret}</code>
          <button
            className="icon-button"
            aria-label="Copy secret"
            onClick={() => void navigator.clipboard?.writeText(secret)}
          >
            <Copy size={14} />
          </button>
          <small>
            Shown once. Send as Bearer token or use as the GitHub/Linear signing
            secret.
          </small>
        </div>
      )}
      <div className="task-controls">
        <button
          onClick={() =>
            void run(async () =>
              setSecret(
                (
                  await api<{ secret: string }>(
                    `/triggers/${trigger.id}/rotate`,
                    'POST',
                    {},
                  )
                ).secret,
              ),
            )
          }
        >
          <RotateCw size={13} />
          New secret
        </button>
        <button
          className="quiet-button"
          onClick={() => {
            if (window.confirm(`Delete the "${trigger.name}" trigger?`))
              void run(() => api(`/triggers/${trigger.id}`, 'DELETE'));
          }}
        >
          <Trash2 size={13} />
          Delete
        </button>
      </div>
    </article>
  );
}

function NewTrigger({
  workspace,
  hookBase,
  onCreated,
  onError,
}: {
  workspace: WorkspaceState;
  hookBase: string;
  onCreated: () => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState('');
  const [threadId, setThreadId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [created, setCreated] = useState<{ url: string; secret: string }>();
  return (
    <form
      className="team-card new-trigger"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          const result = await api<{ url: string; secret: string }>(
            '/triggers',
            'POST',
            { name, threadId, prompt },
          );
          setCreated(result);
          setName('');
          setPrompt('');
          onCreated();
        } catch (err) {
          onError(err instanceof Error ? err.message : 'Could not create.');
        }
      }}
    >
      <strong>New webhook trigger</strong>
      <p className="muted">
        Outside services (GitHub, Linear, a script) POST to its URL; each event
        becomes a turn in the chosen conversation. Base: <code>{hookBase}</code>
      </p>
      <input
        aria-label="Trigger name"
        placeholder="New Linear blocker"
        value={name}
        maxLength={80}
        required
        onChange={(e) => setName(e.target.value)}
      />
      <select
        aria-label="Conversation"
        value={threadId}
        required
        onChange={(e) => setThreadId(e.target.value)}
      >
        <option value="" disabled>
          Choose a conversation
        </option>
        {workspace.conversations.map((thread) => (
          <option key={thread.id} value={thread.id}>
            {dotName(workspace.dots, thread.dotId)} · {thread.title}
          </option>
        ))}
      </select>
      <textarea
        aria-label="Instructions"
        rows={3}
        placeholder="Assess the blocker, find its owner, and draft a follow-up for approval."
        value={prompt}
        maxLength={4000}
        required
        onChange={(e) => setPrompt(e.target.value)}
      />
      <button className="primary" disabled={!workspace.conversations.length}>
        Create trigger
      </button>
      {created && (
        <div className="hook-url secret">
          <code>{created.url}</code>
          <code>{created.secret}</code>
          <small>Copy the secret now; it is shown once.</small>
        </div>
      )}
    </form>
  );
}

export function TeamActivity({
  tab,
  team,
  workspace,
  reload,
  onError,
}: {
  tab: 'approvals' | 'team' | 'webhooks';
  team?: TeamResponse;
  workspace: WorkspaceState;
  reload: () => Promise<void>;
  onError: (message: string) => void;
}) {
  if (!team) return <p className="muted">Loading team activity…</p>;
  if (tab === 'approvals')
    return (
      <div className="team-list">
        {team.approvals.map((approval) => (
          <ApprovalCard
            key={approval.id}
            approval={approval}
            dots={workspace.dots}
            onDecide={async (decision) => {
              try {
                await api(`/approvals/${approval.id}/decision`, 'POST', {
                  decision,
                });
                await reload();
              } catch (e) {
                onError(e instanceof Error ? e.message : 'Could not decide.');
              }
            }}
          />
        ))}
        {!team.approvals.length && (
          <div className="large-empty">
            <ShieldCheck size={32} />
            <h2>Nothing waiting for you.</h2>
            <p>
              Dots under the Reversibility Law read, research, and draft on
              their own. Anything that sends, publishes, pays, or deletes waits
              here for your sign-off.
            </p>
          </div>
        )}
      </div>
    );
  if (tab === 'team') {
    const groups = new Map<string, Delegation[]>();
    for (const item of [...team.delegations].reverse())
      groups.set(item.groupId, [...(groups.get(item.groupId) ?? []), item]);
    const ordered = [...groups.values()].reverse();
    return (
      <div className="team-list">
        {ordered.map((items) => (
          <DelegationGroup
            key={items[0].groupId}
            items={items}
            team={team}
            dots={workspace.dots}
            onCancel={(id) =>
              void api(`/delegations/${id}/cancel`, 'POST', {})
                .then(reload)
                .catch((e) => onError(e.message))
            }
          />
        ))}
        {!ordered.length && (
          <div className="large-empty">
            <Workflow size={32} />
            <h2>No handoffs yet.</h2>
            <p>
              Ask your Chief of Staff for something bigger than one job. Each
              brief it sends a specialist shows up here with its activity and
              deliverable.
            </p>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="team-list">
      <NewTrigger
        workspace={workspace}
        hookBase={team.hookBase}
        onCreated={() => void reload()}
        onError={onError}
      />
      {team.triggers.map((trigger) => (
        <TriggerCard
          key={trigger.id}
          trigger={trigger}
          hookBase={team.hookBase}
          workspace={workspace}
          onChange={() => void reload()}
          onError={onError}
        />
      ))}
    </div>
  );
}
