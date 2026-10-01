import { useEffect, useRef, useState } from 'react';
import { Check, Copy, Users, X } from 'lucide-react';
import type { WorkspaceState } from '../shared/types';
import { api } from './api';
import type { TeamResponse } from './TeamActivity';

interface BlueprintResult {
  task?: { cron: string | null; timezone: string | null };
  trigger?: { url: string; secret: string; trigger: { name: string } };
  needs: string[];
}

const browserZone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};

export function TeamDialog({
  workspace,
  team,
  onClose,
  onChanged,
}: {
  workspace: WorkspaceState;
  team?: TeamResponse;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [chief, setChief] = useState(workspace.setup.defaultModel ?? '');
  const [worker, setWorker] = useState(workspace.setup.workerModel ?? '');
  const [timezone, setTimezone] = useState(
    workspace.setup.timezone && workspace.setup.timezone !== 'UTC'
      ? workspace.setup.timezone
      : browserZone(),
  );
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [installed, setInstalled] = useState<Record<string, BlueprintResult>>(
    {},
  );
  const container = useRef<HTMLElement>(null);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', key);
    container.current?.querySelector<HTMLElement>('input')?.focus();
    return () => document.removeEventListener('keydown', key);
  }, [onClose]);
  const names = new Set(workspace.dots.map((dot) => dot.name.toLowerCase()));
  const hasTeam = !!team?.roster.every((card) =>
    names.has(card.name.toLowerCase()),
  );
  const configured = workspace.setup.missing.length === 0;
  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    setError('');
    try {
      await fn();
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.');
    } finally {
      setBusy('');
    }
  };
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        className="modal team-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="team-title"
        ref={container}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          className="modal-close icon-button"
          aria-label="Close dialog"
          onClick={onClose}
        >
          <X size={18} />
        </button>
        <span className="eyebrow">ALWAYS-ON TEAM</span>
        <h2 id="team-title">Hire your first team.</h2>
        <p className="muted">
          A Chief of Staff who delegates, four specialists with their own
          computers, a shared Team HQ space, and approval before anything leaves
          the building.
        </p>
        <div className="config-note">
          <strong>Models</strong>
          <p>
            Providers with keys:{' '}
            {workspace.setup.providers?.length
              ? workspace.setup.providers.join(', ')
              : 'none yet'}
            . Default: {workspace.setup.defaultModel ?? 'not set'}.
          </p>
        </div>
        <datalist id="team-models">
          {team?.models.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
        <label className="field-label" htmlFor="chief-model">
          Chief of Staff model (strong reasoning)
        </label>
        <input
          id="chief-model"
          list="team-models"
          placeholder="anthropic:claude-opus-4-5"
          value={chief}
          onChange={(e) => setChief(e.target.value)}
        />
        <label className="field-label" htmlFor="worker-model">
          Specialist model (fast, cheaper)
        </label>
        <input
          id="worker-model"
          list="team-models"
          placeholder="openai:gpt-5-mini"
          value={worker}
          onChange={(e) => setWorker(e.target.value)}
        />
        <ul className="roster-preview">
          {team?.roster.map((card) => (
            <li key={card.name}>
              {names.has(card.name.toLowerCase()) ? (
                <Check size={13} />
              ) : (
                <Users size={13} />
              )}
              <strong>{card.name}</strong> {card.title}
            </li>
          ))}
        </ul>
        <button
          className="primary full"
          disabled={!!busy || hasTeam}
          onClick={() =>
            void act('team', async () => {
              await api('/team/install', 'POST', {
                chiefModel: chief.trim() || null,
                workerModel: worker.trim() || null,
              });
            })
          }
        >
          {hasTeam
            ? 'Team installed'
            : busy === 'team'
              ? 'Hiring…'
              : 'Install the 5-Dot team'}
        </button>
        <h3 className="team-subhead">Blueprints</h3>
        <p className="muted">
          Each creates a routine conversation with a schedule and/or a webhook
          trigger. Needs the team and full setup.
        </p>
        <label className="field-label" htmlFor="team-timezone">
          Time zone for schedules
        </label>
        <input
          id="team-timezone"
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
        />
        <div className="blueprint-list">
          {team?.blueprints.map((blueprint) => {
            const result = installed[blueprint.id];
            return (
              <article className="team-card" key={blueprint.id}>
                <header>
                  <div>
                    <strong>{blueprint.name}</strong>
                    <span>
                      {blueprint.dot}
                      {blueprint.cron ? ` · ${blueprint.cron}` : ''}
                      {blueprint.trigger ? ' · webhook' : ''}
                    </span>
                  </div>
                  <button
                    disabled={!!busy || !hasTeam || !configured || !!result}
                    onClick={() =>
                      void act(blueprint.id, async () => {
                        const value = await api<BlueprintResult>(
                          `/blueprints/${blueprint.id}/install`,
                          'POST',
                          { timezone },
                        );
                        setInstalled((current) => ({
                          ...current,
                          [blueprint.id]: value,
                        }));
                      })
                    }
                  >
                    {result
                      ? 'Installed'
                      : busy === blueprint.id
                        ? 'Installing…'
                        : 'Install'}
                  </button>
                </header>
                <p>{blueprint.summary}</p>
                {result && (
                  <div className="blueprint-result">
                    {result.trigger && (
                      <div className="hook-url secret">
                        <code>{result.trigger.url}</code>
                        <code>{result.trigger.secret}</code>
                        <button
                          className="icon-button"
                          aria-label="Copy webhook URL and secret"
                          onClick={() =>
                            void navigator.clipboard?.writeText(
                              `${result.trigger!.url}\n${result.trigger!.secret}`,
                            )
                          }
                        >
                          <Copy size={14} />
                        </button>
                        <small>Copy the secret now; it is shown once.</small>
                      </div>
                    )}
                    <ul>
                      {result.needs.map((need) => (
                        <li key={need}>{need}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </article>
            );
          })}
        </div>
        {!configured && (
          <p className="muted">
            Blueprints need {workspace.setup.missing.join(', ')} first.
          </p>
        )}
        {error && (
          <p className="chat-error" role="alert">
            {error}
          </p>
        )}
      </section>
    </div>
  );
}
