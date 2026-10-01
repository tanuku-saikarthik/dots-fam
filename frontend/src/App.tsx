import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity as ActivityIcon, CalendarClock, FileText, Menu, MessageSquare, Monitor, Plus, Settings2 } from 'lucide-react';
import { api, ApiError, setToken, type Activity, type AppState } from './api';
import { DotMark } from './components/DotMark';
import { TeamLine } from './components/TeamLine';
import { ActivityView } from './views/ActivityView';
import { ChatView } from './views/ChatView';
import { ComputerView } from './views/ComputerView';
import { PagesView } from './views/PagesView';
import { RoutinesView } from './views/RoutinesView';
import { TeamView } from './views/TeamView';

function useHashRoute(): [string[], (path: string) => void] {
  const read = () => (location.hash.replace(/^#\/?/, '') || 'chat').split('/').filter(Boolean);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const onChange = () => setParts(read());
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return [parts, (path: string) => (location.hash = path)];
}

function Lock({ onUnlock }: { onUnlock: () => void }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  return (
    <main className="lock">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setToken(value);
          try {
            await api('/state');
            onUnlock();
          } catch {
            setError('That token was not accepted.');
          }
        }}
      >
        <h1>Dots Fam</h1>
        <p className="muted">Enter the owner token from your server's .env.</p>
        <input type="password" aria-label="Owner token" value={value} onChange={(e) => setValue(e.target.value)} required />
        <button className="button primary">Unlock</button>
        {error && <p className="error">{error}</p>}
      </form>
    </main>
  );
}

export function App() {
  const [state, setState] = useState<AppState>();
  const [activity, setActivity] = useState<Activity>();
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState('');
  const [menu, setMenu] = useState(false);
  const [route, navigate] = useHashRoute();
  const [view] = route;

  const refresh = useCallback(async () => {
    try {
      setState(await api<AppState>('/state'));
      setLocked(false);
      setError('');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setLocked(true);
      else setError(e instanceof Error ? e.message : 'Could not reach the server.');
    }
  }, []);
  const refreshActivity = useCallback(async () => {
    try {
      setActivity(await api<Activity>('/activity'));
    } catch {
      /* the state poll reports connection problems */
    }
  }, []);
  const refreshAll = useCallback(() => {
    void refresh();
    void refreshActivity();
  }, [refresh, refreshActivity]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (view !== 'activity' && view !== 'routines') return;
    void refreshActivity();
    const timer = setInterval(() => void refreshActivity(), 3000);
    return () => clearInterval(timer);
  }, [view, refreshActivity]);
  useEffect(() => setMenu(false), [route.join('/')]);

  const chatDot = useMemo(() => {
    if (!state) return undefined;
    if ((view === 'chat' || view === 'computer') && route[1]) return state.dots.find((d) => d.id === route[1]);
    return state.dots.find((d) => d.can_delegate) ?? state.dots[0];
  }, [state, view, route]);

  if (locked) return <Lock onUnlock={() => void refresh()} />;
  if (!state)
    return (
      <main className="lock">
        <div>
          <h1>Dots Fam</h1>
          <p className="muted">{error || 'Connecting to your team…'}</p>
        </div>
      </main>
    );

  const threadId = view === 'chat' ? route[2] : undefined;
  const dotThreads = state.threads.filter((t) => t.dot_id === chatDot?.id);
  const go = (path: string) => navigate(path);

  return (
    <div className="shell">
      <aside className={`sidebar ${menu ? 'open' : ''}`} aria-label="Navigation">
        <div className="brand">
          <span className="brand-dots" aria-hidden="true">
            <i className="c-purple" />
            <i className="c-mint" />
            <i className="c-orange" />
            <i className="c-blue" />
          </span>
          Dots Fam
        </div>
        <nav className="nav">
          <button aria-current={view === 'chat' ? 'page' : undefined} onClick={() => go(`/chat/${chatDot?.id ?? ''}`)}>
            <MessageSquare size={17} /> Chat
          </button>
          <button aria-current={view === 'activity' ? 'page' : undefined} onClick={() => go('/activity')}>
            <ActivityIcon size={17} /> Activity
            {!!state.pending_approvals && <span className="count">{state.pending_approvals}</span>}
          </button>
          <button aria-current={view === 'routines' ? 'page' : undefined} onClick={() => go('/routines')}>
            <CalendarClock size={17} /> Routines
          </button>
          <button aria-current={view === 'pages' ? 'page' : undefined} onClick={() => go('/pages')}>
            <FileText size={17} /> Pages
          </button>
          <button aria-current={view === 'computer' ? 'page' : undefined} onClick={() => go(`/computer/${chatDot?.id ?? ''}`)}>
            <Monitor size={17} /> Computers
          </button>
          <button aria-current={view === 'team' ? 'page' : undefined} onClick={() => go('/team')}>
            <Settings2 size={17} /> Team and setup
          </button>
        </nav>
        {chatDot && (
          <div className="threads">
            <div className="threads-head">
              <span>Conversations with {chatDot.name}</span>
              <button className="button ghost" aria-label="New conversation" onClick={() => go(`/chat/${chatDot.id}`)}>
                <Plus size={16} />
              </button>
            </div>
            {dotThreads.map((thread) => (
              <button
                key={thread.id}
                className="thread-item"
                aria-current={thread.id === threadId}
                onClick={() => go(`/chat/${chatDot.id}/${thread.id}`)}
              >
                <span>{thread.title}</span>
                {thread.pending_approvals ? (
                  <i className="flag wait" title="Waiting for your approval" />
                ) : thread.running ? (
                  <i className="flag" title="Working" />
                ) : null}
              </button>
            ))}
            {!dotThreads.length && <p className="muted small" style={{ padding: '0 8px' }}>None yet.</p>}
          </div>
        )}
        <div className="sidebar-foot muted">
          <DotMark dot={{ name: 'Y', color: 'blue' }} size="s" />
          {state.flags.paused ? 'Team paused' : `${state.dots.length} Dots on duty`}
        </div>
      </aside>
      <main className="main">
        <TeamLine
          state={state}
          selected={view === 'chat' ? chatDot?.id : undefined}
          onSelect={(dot) => go(`/chat/${dot.id}`)}
          onPause={() => void api('/settings', 'PATCH', { paused: !state.flags.paused }).then(refresh)}
          menu={
            <button className="button ghost menu-toggle" aria-label="Open navigation" onClick={() => setMenu(true)}>
              <Menu size={18} />
            </button>
          }
        />
        {error && <p className="error" style={{ margin: '12px 28px 0' }}>{error}</p>}
        {state.flags.paused && (
          <p className="error" style={{ margin: '12px 28px 0' }}>
            The team is paused. Running work stopped and routines wait until you resume.
          </p>
        )}
        {view === 'activity' ? (
          <ActivityView state={state} activity={activity} reload={refreshAll} />
        ) : view === 'routines' ? (
          <RoutinesView state={state} activity={activity} reload={refreshAll} threadId={route[1]} />
        ) : view === 'pages' ? (
          <PagesView state={state} spaceId={route[1]} pageId={route[2]} navigate={go} />
        ) : view === 'computer' ? (
          <ComputerView state={state} dotId={route[1]} navigate={go} />
        ) : view === 'team' ? (
          <TeamView state={state} onChanged={refreshAll} />
        ) : chatDot ? (
          <ChatView
            key={chatDot.id}
            state={state}
            dot={chatDot}
            threadId={threadId}
            onThread={(id) => go(id ? `/chat/${chatDot.id}/${id}` : `/chat/${chatDot.id}`)}
            onRefresh={refresh}
            onRoutine={(id) => go(`/routines/${id}`)}
            onComputer={() => go(`/computer/${chatDot.id}`)}
          />
        ) : (
          <div className="empty">
            <h2>No Dots yet</h2>
            <button className="button primary" onClick={() => go('/team')}>
              Set up your team
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
