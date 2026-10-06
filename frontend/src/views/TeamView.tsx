import { useEffect, useRef, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { api, type AppState, type Dot } from '../api';
import { browserZone } from '../cron';
import { DotMark } from '../components/DotMark';
import { CallsPanel } from '../components/CallsPanel';

const COLORS = ['purple', 'mint', 'orange', 'blue', 'ochre', 'rose'];

interface TeamInfo {
  roster: { name: string; title: string; chief: boolean }[];
  blueprints: { id: string; name: string; summary: string; dot: string; cron: string | null; trigger: boolean; needs: string[] }[];
  hook_base: string;
}

function DotForm({ state, dot, onSaved }: { state: AppState; dot?: Dot; onSaved: (dot: Dot) => void }) {
  const blank = {
    name: '',
    title: '',
    instructions: '',
    model: '',
    can_delegate: false,
    approval_mode: 'reversible' as const,
    research_allowed: true,
    memory_allowed: true,
    color: 'blue',
    space_ids: state.spaces.map((s) => s.id),
  };
  const [form, setForm] = useState(() =>
    dot ? { ...dot, model: dot.model ?? '' } : { ...blank, space_id: state.spaces[0]?.id ?? '' },
  );
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const lastDotId = useRef(dot?.id);
  if (dot?.id !== lastDotId.current) {
    lastDotId.current = dot?.id;
    setForm(dot ? { ...dot, model: dot.model ?? '' } : { ...blank, space_id: state.spaces[0]?.id ?? '' });
    setError('');
    setSaved(false);
  }
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setSaved(false);
    setForm((current) => ({ ...current, [key]: value }));
  };
  return (
    <form
      className="panel stack"
      onSubmit={async (e) => {
        e.preventDefault();
        setError('');
        const body = {
          name: form.name,
          title: form.title,
          instructions: form.instructions,
          model: form.model.trim() || null,
          can_delegate: form.can_delegate,
          approval_mode: form.approval_mode,
          research_allowed: form.research_allowed,
          memory_allowed: form.memory_allowed,
          color: form.color,
          space_id: form.space_id,
          space_ids: form.space_ids,
        };
        try {
          const result = dot
            ? await api<Dot>(`/dots/${dot.id}`, 'PATCH', { ...body, model: form.model.trim() })
            : await api<Dot>('/dots', 'POST', body);
          setSaved(true);
          onSaved(result);
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Could not save.');
        }
      }}
    >
      <div className="row">
        <DotMark dot={{ name: form.name || '?', color: form.color }} size="l" />
        <h2 className="grow">{dot ? `Edit ${dot.name}` : 'New Dot'}</h2>
        {dot && (
          <button
            type="button"
            className="button ghost danger"
            onClick={async () => {
              if (!window.confirm(`Remove ${dot.name} and their conversations?`)) return;
              await api(`/dots/${dot.id}`, 'DELETE');
              onSaved(dot);
            }}
          >
            <Trash2 size={15} /> Remove
          </button>
        )}
      </div>
      <div className="form-grid">
        <label className="field">
          <span>Name</span>
          <input value={form.name} required maxLength={40} onChange={(e) => set('name', e.target.value)} />
        </label>
        <label className="field">
          <span>Title</span>
          <input value={form.title} maxLength={60} placeholder="Lead Prospector" onChange={(e) => set('title', e.target.value)} />
        </label>
        <label className="field full">
          <span>Role card</span>
          <textarea
            rows={9}
            required
            minLength={3}
            maxLength={4000}
            value={form.instructions}
            placeholder="Goal, sources, working style, approval boundary, cadence."
            onChange={(e) => set('instructions', e.target.value)}
          />
          <small>The Chief of Staff routes work from this card, so make the goal and the boundary specific.</small>
        </label>
        <label className="field">
          <span>Model</span>
          <input
            list="models"
            value={form.model}
            placeholder={`Default: ${state.setup.default_model ?? 'not set'}`}
            onChange={(e) => set('model', e.target.value)}
          />
          <datalist id="models">
            {state.models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <small>provider:model, e.g. anthropic:claude-haiku-4-5</small>
        </label>
        <div className="field">
          <span>Color</span>
          <div className="color-pick">
            {COLORS.map((color) => (
              <button type="button" key={color} aria-label={color} aria-pressed={form.color === color} onClick={() => set('color', color)}>
                <DotMark dot={{ name: ' ', color }} />
              </button>
            ))}
          </div>
        </div>
        <label className="check full">
          <input type="checkbox" checked={form.can_delegate} onChange={(e) => set('can_delegate', e.target.checked)} />
          <span>
            <strong>Chief of Staff</strong>
            <small>Can hand briefs to the other Dots in parallel and merge what comes back.</small>
          </span>
        </label>
        <label className="check full">
          <input
            type="checkbox"
            checked={form.approval_mode === 'reversible'}
            onChange={(e) => set('approval_mode', e.target.checked ? 'reversible' : 'autonomous')}
          />
          <span>
            <strong>Ask before acting outside</strong>
            <small>Sending, posting, submitting, paying, deleting or pushing waits for your approval.</small>
          </span>
        </label>
        <label className="check">
          <input type="checkbox" checked={form.research_allowed} onChange={(e) => set('research_allowed', e.target.checked)} />
          <span>
            <strong>{state.setup.web_search ? 'Search and read the web' : 'Read web pages'}</strong>
            <small>
              {state.setup.web_search
                ? 'Searches, reads and crawls pages with Exa when a question needs current facts.'
                : 'Reads pages by link. Add EXA_API_KEY to .env to let Dots search the web.'}
            </small>
          </span>
        </label>
        <label className="check">
          <input type="checkbox" checked={form.memory_allowed} onChange={(e) => set('memory_allowed', e.target.checked)} />
          <span>
            <strong>Use your preferences</strong>
          </span>
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button className="button primary">{dot ? 'Save changes' : 'Add Dot'}</button>
        {saved && <span className="muted small">Saved.</span>}
      </div>
    </form>
  );
}

function LocalCapability({ dot, state, onChanged }: { dot: Dot; state: AppState; onChanged: (dot: Dot) => void }) {
  const [enabled, setEnabled] = useState(!!dot.local?.enabled);
  const [dir, setDir] = useState(dot.local?.project_dir ?? '');
  const [mode, setMode] = useState<'ask' | 'build'>(dot.local?.mode ?? 'ask');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const lastDotId = useRef(dot.id);
  if (dot.id !== lastDotId.current) {
    lastDotId.current = dot.id;
    setEnabled(!!dot.local?.enabled);
    setDir(dot.local?.project_dir ?? '');
    setMode(dot.local?.mode ?? 'ask');
    setError('');
    setSaved(false);
  }
  return (
    <form
      className="panel stack"
      onSubmit={async (e) => {
        e.preventDefault();
        setError('');
        setSaved(false);
        try {
          const result = await api<Dot>(`/local/${dot.id}`, 'PATCH', {
            enabled,
            project_dir: dir.trim() || null,
            mode,
          });
          setSaved(true);
          onChanged(result);
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Could not save.');
        }
      }}
    >
      <div>
        <h2>Claude Code capabilities</h2>
        <p className="muted small">
          One switch for everything Claude Code can do on a real task: reading, writing and editing files,
          running commands, searching the web, and using its own browser — all scoped to one project folder on
          this computer. Turning it on also switches on "Search and read the web" and the Computer for this Dot.
        </p>
      </div>
      <label className="check full">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>
          <strong>Give {dot.name} local Claude Code access</strong>
          <small>
            Reading is free, except files that may hold secrets (.env, keys). Writing, editing, and any
            command that isn&apos;t simple and read-only wait for your approval, same Reversibility Law as
            everything else.
          </small>
        </span>
      </label>
      <label className="field full">
        <span>Project folder</span>
        <input
          value={dir}
          placeholder="/mnt/c/Users/you/projects/this-project"
          onChange={(e) => setDir(e.target.value)}
        />
        <small>
          Free reads and read-only commands stay inside this folder. Anything that reaches outside it asks you
          first.
        </small>
      </label>
      <label className="check full">
        <input type="checkbox" checked={mode === 'build'} onChange={(e) => setMode(e.target.checked ? 'build' : 'ask')} />
        <span>
          <strong>Build mode: {dot.name} codes on its own git branch</strong>
          <small>
            Writing files, installing, building, testing and committing are free, on a branch in a separate
            copy of the project. Your checkout and .env are never touched. {dot.name} only asks before pushing
            the branch and opening a pull request. The project folder must be a git repository.
            {state.setup.build_sandbox === false &&
              ' Docker was not found on this server, so commands will run directly on it and ask first.'}
          </small>
        </span>
      </label>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button className="button primary">Save changes</button>
        {saved && <span className="muted small">Saved.</span>}
      </div>
    </form>
  );
}

function Blueprints({ state, onChanged }: { state: AppState; onChanged: () => void }) {
  const [info, setInfo] = useState<TeamInfo>();
  const [zone, setZone] = useState(state.setup.timezone !== 'UTC' ? state.setup.timezone : browserZone());
  const [installed, setInstalled] = useState<Record<string, { url?: string; secret?: string; needs: string[] }>>({});
  const [error, setError] = useState('');
  useEffect(() => {
    void api<TeamInfo>('/team').then(setInfo);
  }, []);
  return (
    <section className="stack">
      <div>
        <h2>Blueprints</h2>
        <p className="muted small">Each one creates a routine conversation with a schedule, a webhook, or both.</p>
      </div>
      <label className="field" style={{ maxWidth: 320 }}>
        <span>Time zone for schedules</span>
        <input value={zone} onChange={(e) => setZone(e.target.value)} />
      </label>
      <div className="list">
        {info?.blueprints.map((b) => {
          const result = installed[b.id];
          return (
            <div className="list-item" key={b.id}>
              <div className="grow">
                <strong>{b.name}</strong>
                <p className="muted small">{b.summary}</p>
                {result && (
                  <div className="secret" style={{ marginTop: 8 }}>
                    {result.secret && (
                      <>
                        <strong>Webhook secret, shown once:</strong>
                        <code>{result.url}</code>
                        <code>{result.secret}</code>
                      </>
                    )}
                    {result.needs.map((need) => (
                      <span key={need}>{need}</span>
                    ))}
                  </div>
                )}
              </div>
              <button
                className="button"
                disabled={!!result}
                onClick={async () => {
                  setError('');
                  try {
                    const value = await api<{ url?: string; secret?: string; needs: string[] }>(
                      `/blueprints/${b.id}/install`,
                      'POST',
                      { timezone: zone },
                    );
                    setInstalled((current) => ({ ...current, [b.id]: value }));
                    onChanged();
                  } catch (e) {
                    setError(e instanceof Error ? e.message : 'Could not install.');
                  }
                }}
              >
                {result ? 'Installed' : 'Install'}
              </button>
            </div>
          );
        })}
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

export function TeamView({ state, onChanged }: { state: AppState; onChanged: () => void }) {
  const [selected, setSelected] = useState<string | 'new'>(state.dots[0]?.id ?? 'new');
  const [memory, setMemory] = useState('');
  const dot = state.dots.find((d) => d.id === selected);
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Team and setup</h1>
          <p className="muted">
            {state.setup.missing.length
              ? `Add ${state.setup.missing.join(', ')} to your .env, then restart.`
              : `Models from ${state.setup.providers.join(', ')}. Default ${state.setup.default_model}, specialists ${state.setup.worker_model ?? 'same'}.`}
          </p>
        </div>
        {!state.dots.some((d) => d.can_delegate) && (
          <button className="button primary" onClick={() => void api('/team/install', 'POST', {}).then(onChanged)}>
            Install the 5-Dot team
          </button>
        )}
      </div>
      <div className="team-grid">
        <nav className="dot-picker" aria-label="Dots">
          {state.dots.map((d) => (
            <button key={d.id} aria-current={selected === d.id} onClick={() => setSelected(d.id)}>
              <DotMark dot={d} size="s" />
              {d.name}
            </button>
          ))}
          <button aria-current={selected === 'new'} onClick={() => setSelected('new')}>
            <Plus size={16} /> New Dot
          </button>
        </nav>
        <div className="stack">
          <DotForm
            state={state}
            dot={dot}
            onSaved={(saved) => {
              onChanged();
              if (!dot) setSelected(saved.id);
            }}
          />
          {dot && (
            <LocalCapability
              dot={dot}
              state={state}
              onChanged={(saved) => {
                onChanged();
                setSelected(saved.id);
              }}
            />
          )}
          <CallsPanel />
          <Blueprints state={state} onChanged={onChanged} />
          <section className="stack">
            <div>
              <h2>Your preferences</h2>
              <p className="muted small">Dots that use your preferences read these on every turn. Avoid secrets.</p>
            </div>
            <form
              className="row"
              onSubmit={async (e) => {
                e.preventDefault();
                await api('/memories', 'POST', { text: memory });
                setMemory('');
                onChanged();
              }}
            >
              <input
                className="grow"
                aria-label="New preference"
                placeholder="Keep briefs short and scannable."
                value={memory}
                required
                onChange={(e) => setMemory(e.target.value)}
              />
              <button className="button">Add</button>
            </form>
            {!!state.memories.length && (
              <div className="list">
                {state.memories.map((m) => (
                  <div className="list-item" key={m.id}>
                    <span className="grow">{m.text}</span>
                    <button
                      className="button ghost danger"
                      aria-label="Delete preference"
                      onClick={() => void api(`/memories/${m.id}`, 'DELETE').then(onChanged)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
