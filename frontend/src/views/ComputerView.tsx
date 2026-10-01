import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type WheelEvent } from 'react';
import { ArrowRight, Folder, FileText, Hand, Power, PowerOff, Undo2 } from 'lucide-react';
import { api, apiBlob, type AppState, type ComputerStatus, type Dot, type FileEntry } from '../api';
import { DotMark } from '../components/DotMark';

const WIDTH = 1280;
const HEIGHT = 800;
const SPECIAL = new Set([
  'Enter', 'Backspace', 'Tab', 'Escape', 'Delete', 'Home', 'End', 'PageUp', 'PageDown',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
]);

function useScreen(dotId: string, live: boolean) {
  const [src, setSrc] = useState<string>();
  const current = useRef<string | undefined>(undefined);
  const grab = useCallback(async () => {
    const blob = await apiBlob(`/computers/${dotId}/screen`).catch(() => undefined);
    if (!blob) return;
    const next = URL.createObjectURL(blob);
    if (current.current) URL.revokeObjectURL(current.current);
    current.current = next;
    setSrc(next);
  }, [dotId]);
  useEffect(() => {
    if (!live) return;
    void grab();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') void grab();
    }, 1500);
    return () => clearInterval(timer);
  }, [live, grab]);
  useEffect(
    () => () => {
      if (current.current) URL.revokeObjectURL(current.current);
    },
    [],
  );
  return { src: live ? src : undefined, grab };
}

function Files({ dot }: { dot: Dot }) {
  const [path, setPath] = useState('');
  const [entries, setEntries] = useState<FileEntry[]>();
  const [open, setOpen] = useState<{ path: string; content: string }>();
  const [error, setError] = useState('');
  useEffect(() => {
    api<{ entries: FileEntry[] }>(`/computers/${dot.id}/files?path=${encodeURIComponent(path)}`)
      .then((data) => {
        setEntries(data.entries);
        setError('');
      })
      .catch((e: Error) => setError(e.message));
  }, [dot.id, path]);
  return (
    <section className="panel stack" aria-label="Workspace files">
      <div className="row">
        <h3 className="grow">Workspace{path ? ` / ${path}` : ''}</h3>
        {path && (
          <button className="button ghost" onClick={() => setPath(path.split('/').slice(0, -1).join('/'))}>
            <Undo2 size={14} /> Up
          </button>
        )}
      </div>
      {error ? (
        <p className="muted small">{error}</p>
      ) : !entries ? (
        <p className="muted small">Loading…</p>
      ) : !entries.length ? (
        <p className="muted small">Empty. Files {dot.name} saves while working show up here.</p>
      ) : (
        <div className="file-list">
          {entries.map((entry) => {
            const full = path ? `${path}/${entry.name}` : entry.name;
            return (
              <button
                key={entry.name}
                onClick={async () => {
                  if (entry.dir) return setPath(full);
                  const file = await api<{ content?: string; base64?: string }>(
                    `/computers/${dot.id}/files/read?path=${encodeURIComponent(full)}`,
                  );
                  setOpen({ path: full, content: file.content ?? '(binary file)' });
                }}
              >
                {entry.dir ? <Folder size={14} /> : <FileText size={14} />}
                <span className="grow">{entry.name}</span>
                {entry.size != null && <span className="muted small">{Math.max(1, Math.round(entry.size / 1024))} KB</span>}
              </button>
            );
          })}
        </div>
      )}
      {open && (
        <div className="stack">
          <div className="row">
            <strong className="grow">{open.path}</strong>
            <button className="button ghost" onClick={() => setOpen(undefined)}>
              Close
            </button>
          </div>
          <pre className="code">{open.content}</pre>
        </div>
      )}
    </section>
  );
}

function Computer({ dot }: { dot: Dot }) {
  const [status, setStatus] = useState<ComputerStatus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [address, setAddress] = useState('');
  const queue = useRef(Promise.resolve());
  const wheel = useRef({ dy: 0, timer: 0 });
  const holding = status?.holder === 'human';
  const running = !!status?.running;
  const screen = useScreen(dot.id, running);

  const load = useCallback(async () => {
    try {
      setStatus(await api<ComputerStatus>(`/computers/${dot.id}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not reach the computer.');
    }
  }, [dot.id]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [load]);
  useEffect(() => setAddress(status?.url && status.url !== 'about:blank' ? status.url : ''), [status?.url, holding]);

  const act = async (path: string, method = 'POST', body?: unknown) => {
    setBusy(true);
    setError('');
    try {
      const next = await api<ComputerStatus>(`/computers/${dot.id}${path}`, method, body);
      if ('available' in next) setStatus(next);
      else await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not work.');
    } finally {
      setBusy(false);
    }
  };
  const human = (action: string, body: unknown) => {
    queue.current = queue.current
      .then(() => api(`/computers/${dot.id}/human/${action}`, 'POST', body))
      .then(() => void screen.grab())
      .catch((e: Error) => setError(e.message));
  };

  if (!status) return <p className="muted">{error || 'Loading…'}</p>;
  if (!status.available)
    return (
      <div className="empty">
        <h2>Computers are off on this server</h2>
        <p>
          Give each Dot its own browser and workspace: build the image with{' '}
          <code>docker build -f computer/Dockerfile -t dotsfam-computer .</code>, set <code>COMPUTER_DRIVER=docker</code> in{' '}
          <code>.env</code> and restart. For development, <code>COMPUTER_DRIVER=local</code> runs one headless browser per Dot
          on this machine.
        </p>
      </div>
    );

  const perms = status.permissions!;
  const toggle = (key: keyof typeof perms) => void act('', 'PATCH', { [key]: !perms[key] });
  const point = (e: MouseEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    return {
      x: Math.round(((e.clientX - box.left) / box.width) * WIDTH),
      y: Math.round(((e.clientY - box.top) / box.height) * HEIGHT),
    };
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!holding) return;
    const mods = [e.ctrlKey && 'Control', e.altKey && 'Alt', e.metaKey && 'Meta'].filter(Boolean);
    if (e.key.length === 1 && !mods.length) human('type', { text: e.key });
    else if (SPECIAL.has(e.key) || (mods.length && e.key.length === 1))
      human('key', { key: [...mods, e.shiftKey && mods.length ? 'Shift' : '', e.key].filter(Boolean).join('+') });
    else return;
    e.preventDefault();
  };
  const onWheel = (e: WheelEvent<HTMLDivElement>) => {
    if (!holding) return;
    wheel.current.dy += e.deltaY;
    if (wheel.current.timer) return;
    wheel.current.timer = window.setTimeout(() => {
      human('scroll', { dy: Math.round(wheel.current.dy) });
      wheel.current = { dy: 0, timer: 0 };
    }, 180);
  };

  return (
    <div className="computer-layout">
      <div className="stack">
        <div className="computer-bar">
          <span className={`status ${holding ? 'pending' : running ? 'running' : 'cancelled'}`}>
            {holding ? 'You have control' : running ? 'On' : perms.enabled ? 'Off' : 'Turned off'}
          </span>
          {holding ? (
            <form
              className="grow address"
              onSubmit={(e) => {
                e.preventDefault();
                const url = /^https?:\/\//.test(address) ? address : `https://${address}`;
                human('navigate', { url });
              }}
            >
              <input aria-label="Address" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="https://" />
              <button className="button" aria-label="Go">
                <ArrowRight size={15} />
              </button>
            </form>
          ) : (
            <span className="grow muted small url">{status.url && status.url !== 'about:blank' ? status.url : ''}</span>
          )}
          {running &&
            (holding ? (
              <button className="button primary" disabled={busy} onClick={() => void act('/control/release')}>
                <Undo2 size={15} /> Hand back to {dot.name}
              </button>
            ) : (
              <button className="button" disabled={busy} onClick={() => void act('/control/take')}>
                <Hand size={15} /> Take control
              </button>
            ))}
          {running ? (
            <button className="button ghost" disabled={busy || holding} onClick={() => void act('/stop')} aria-label="Turn off computer">
              <PowerOff size={15} />
            </button>
          ) : (
            <button className="button" disabled={busy || !perms.enabled} onClick={() => void act('/start')}>
              <Power size={15} /> Start
            </button>
          )}
        </div>
        {error && <p className="error">{error}</p>}
        <div
          className={`screen ${holding ? 'live' : ''}`}
          tabIndex={holding ? 0 : -1}
          role={holding ? 'application' : undefined}
          aria-label={holding ? `${dot.name}'s browser. Click and type to control it.` : `${dot.name}'s browser`}
          onClick={(e) => holding && human('click', point(e))}
          onKeyDown={onKey}
          onWheel={onWheel}
        >
          {screen.src ? (
            <img src={screen.src} alt={`What ${dot.name}'s browser shows`} draggable={false} />
          ) : (
            <div className="screen-off">
              <DotMark dot={dot} />
              <p>
                {!perms.enabled
                  ? `${dot.name}'s computer is turned off. Switch it on under Access to let ${dot.name} browse and keep files.`
                  : running
                    ? 'Starting the browser…'
                    : `Off for now. ${dot.name} starts it when a task needs a browser, or start it yourself.`}
              </p>
            </div>
          )}
        </div>
        <p className="muted small">
          {holding
            ? `${dot.name} waits while you have control. Log in or fix things, then hand it back.`
            : `Logins stay in ${dot.name}'s browser. Anything that sends, posts, buys or deletes still asks you first.`}
        </p>
      </div>
      <div className="stack">
        <section className="panel stack" aria-label="Access">
          <h3>Access</h3>
          <label className="check">
            <input type="checkbox" checked={perms.enabled} disabled={busy} onChange={() => toggle('enabled')} />
            <span>
              <strong>Computer on</strong>
              <small>{dot.name} can use its own browser and workspace.</small>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={perms.browser} disabled={busy || !perms.enabled} onChange={() => toggle('browser')} />
            <span>
              <strong>Browser</strong>
              <small>Open sites, read, click and fill forms.</small>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={perms.files} disabled={busy || !perms.enabled} onChange={() => toggle('files')} />
            <span>
              <strong>Files</strong>
              <small>Read and write in its own workspace.</small>
            </span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={perms.shell}
              disabled={busy || !perms.enabled || status.driver !== 'docker'}
              onChange={() => toggle('shell')}
            />
            <span>
              <strong>Shell</strong>
              <small>
                {status.driver === 'docker'
                  ? 'Run commands inside its container. Pushes, deploys and posts still ask first.'
                  : 'Only available with Docker computers, so commands never run on your machine.'}
              </small>
            </span>
          </label>
        </section>
        <section className="panel stack" aria-label="Computer activity">
          <h3>Activity</h3>
          {status.activity?.length ? (
            <div className="timeline">
              {status.activity.map((item) => (
                <span key={item.id}>
                  <time>{new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
                  {item.text}
                </span>
              ))}
            </div>
          ) : (
            <p className="muted small">Nothing yet.</p>
          )}
        </section>
        {running && perms.files && <Files dot={dot} />}
      </div>
    </div>
  );
}

export function ComputerView({ state, dotId, navigate }: { state: AppState; dotId?: string; navigate: (path: string) => void }) {
  const dot = state.dots.find((d) => d.id === dotId) ?? state.dots[0];
  if (!dot) return null;
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Computers</h1>
          <p className="muted">Each Dot has its own browser and workspace. Watch what it is doing, or take the wheel.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {state.dots.map((d) => (
          <button key={d.id} role="tab" aria-selected={d.id === dot.id} onClick={() => navigate(`/computer/${d.id}`)}>
            <span className="row" style={{ gap: 8 }}>
              <DotMark dot={d} size="s" />
              {d.name}
              {d.computer?.enabled && <i className="flag" title="Computer on" />}
            </span>
          </button>
        ))}
      </div>
      <Computer key={dot.id} dot={dot} />
    </div>
  );
}
