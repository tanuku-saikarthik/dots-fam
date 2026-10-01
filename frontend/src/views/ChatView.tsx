import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertTriangle, ArrowUp, CircleCheck, CircleDot, Clock, Monitor, Square, Trash2, Wrench } from 'lucide-react';
import {
  api,
  streamUrl,
  type AppState,
  type Dot,
  type Message,
  type RunEvent,
  type ThreadDetail,
} from '../api';
import { ApprovalCard } from '../components/ApprovalCard';
import { DotMark } from '../components/DotMark';

const STARTERS: Record<string, string[]> = {
  chief: [
    'Plan this week: what should the team work on first?',
    'Find three companies hiring AI engineers in Bengaluru and draft intros.',
    'Summarize Team HQ and tell me what is missing.',
  ],
  worker: ['What can you do for me?', 'Read the Launch Brief and suggest improvements.'],
};

const EVENT_LABELS: Record<string, string> = {
  team: 'Team update: delegated work came back',
  routine: 'Scheduled run started',
  trigger: 'Webhook event arrived',
  approval: 'Your approval decision',
  delegation: 'Brief from the Chief of Staff',
  slack: 'Message from Slack',
  voice: 'From a voice call',
};

const VIA: Record<string, string> = { slack: 'via Slack', voice: 'via voice call' };

const LABELS: Record<string, string> = {
  list_spaces: 'Looked at Spaces',
  list_pages: 'Listed pages',
  read_page: 'Read page',
  create_page: 'Created page',
  update_page: 'Updated page',
  read_web_page: 'Read web page',
  delegate_tasks: 'Handed off',
  computer_open: 'Opened in browser',
  computer_snapshot: 'Looked at the page',
  computer_read: 'Read the page',
  computer_click: 'Clicked',
  computer_type: 'Filled in a field',
  computer_press: 'Pressed a key',
  computer_scroll: 'Scrolled',
  computer_list_files: 'Listed files',
  computer_read_file: 'Read file',
  computer_write_file: 'Saved file',
  computer_shell: 'Ran a command',
};

function argLabel(args: Record<string, unknown>) {
  for (const key of ['title', 'page', 'url', 'path', 'channel']) if (typeof args[key] === 'string') return String(args[key]);
  return '';
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function Steps({ message, results, dots }: { message: Message; results: Map<string, Message>; dots: Dot[] }) {
  return (
    <div className="steps">
      {message.tool_calls?.map((call) => {
        const result = results.get(call.id);
        if (call.name === 'delegate_tasks') {
          const assignments = (call.args.assignments as { dot: string }[] | undefined) ?? [];
          return (
            <div className="handoff" key={call.id}>
              <span>Handed off to</span>
              {assignments.map((a, i) => {
                const target = dots.find((d) => d.name.toLowerCase() === a.dot.toLowerCase());
                return (
                  <span className="row" key={i} style={{ gap: 6 }}>
                    {i > 0 && <span className="line" />}
                    <DotMark dot={target ?? { name: a.dot, color: 'blue' }} size="s" />
                    {target?.name ?? a.dot}
                  </span>
                );
              })}
              {result && !result.ok && <span className="step failed">failed</span>}
            </div>
          );
        }
        const failed = result && !result.ok;
        return (
          <div className={`step ${failed ? 'failed' : ''}`} key={call.id}>
            {failed ? <AlertTriangle size={14} /> : result ? <CircleCheck size={14} /> : <Wrench size={14} />}
            <span>
              {LABELS[call.name] ?? call.name.replaceAll('_', ' ')}
              {argLabel(call.args) && <> “{argLabel(call.args)}”</>}
              {failed && result.text.startsWith('The owner declined') && ' (declined)'}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export { Markdown };

export function ChatView({
  state,
  dot,
  threadId,
  onThread,
  onRefresh,
  onRoutine,
  onComputer,
}: {
  state: AppState;
  dot: Dot;
  threadId?: string;
  onThread: (id: string | undefined) => void;
  onRefresh: () => void;
  onRoutine: (threadId: string) => void;
  onComputer: () => void;
}) {
  const [detail, setDetail] = useState<ThreadDetail>();
  const [live, setLive] = useState<{ text: string; steps: RunEvent[] } | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!threadId) {
      setDetail(undefined);
      return;
    }
    try {
      const next = await api<ThreadDetail>(`/threads/${threadId}`);
      setDetail(next);
      if (!next.running) setLive(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load this conversation.');
    }
  }, [threadId]);

  useEffect(() => {
    setLive(null);
    setError('');
    void load();
  }, [load]);

  useEffect(() => {
    if (!threadId) return;
    const source = new EventSource(streamUrl(threadId));
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent;
      if (event.type === 'run.started') setLive({ text: '', steps: [] });
      else if (event.type === 'message.delta')
        setLive((current) => ({ text: (current?.text ?? '') + String(event.delta ?? ''), steps: current?.steps ?? [] }));
      else if (event.type === 'tool.started' || event.type === 'tool.finished')
        setLive((current) => ({
          text: '',
          steps: [...(current?.steps ?? []).filter((s) => !(s.id === event.id && event.type === 'tool.finished')), event],
        }));
      else if (event.type === 'approval.requested' || event.type === 'run.finished') {
        void load();
        onRefresh();
      }
    };
    return () => source.close();
  }, [threadId, load, onRefresh]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [detail, live]);

  const send = async (text: string) => {
    if (!text.trim()) return;
    setBusy(true);
    setError('');
    try {
      let id = threadId;
      if (!id) {
        const thread = await api<{ id: string }>('/threads', 'POST', { dot_id: dot.id });
        id = thread.id;
        onThread(id);
      }
      setLive({ text: '', steps: [] });
      setDetail((current) =>
        current ? { ...current, messages: [...current.messages, { id: null, role: 'user', text }] } : current,
      );
      await api(`/threads/${id}/messages`, 'POST', { text });
      setDraft('');
      onRefresh();
      if (id === threadId) await load();
    } catch (e) {
      setLive(null);
      setError(e instanceof Error ? e.message : 'Could not send.');
    } finally {
      setBusy(false);
    }
  };

  const messages = detail?.messages ?? [];
  const results = new Map(messages.filter((m) => m.role === 'tool').map((m) => [m.tool_call_id ?? '', m]));
  const running = !!detail?.running || !!live;
  const pending = detail?.approvals ?? [];
  const thread = detail?.thread;
  const starters = STARTERS[dot.can_delegate ? 'chief' : 'worker'];

  return (
    <section className="chat" aria-label={`Conversation with ${dot.name}`}>
      <div className="chat-head">
        <DotMark dot={dot} />
        <div className="grow">
          <h2>{thread ? thread.title : `New conversation with ${dot.name}`}</h2>
          <p className="muted small">
            {dot.title || 'Specialist'}, running {dot.model ?? state.setup.default_model ?? 'no model yet'}
          </p>
        </div>
        {dot.computer?.enabled && state.setup.computer_driver !== 'none' && (
          <button className="button ghost" onClick={onComputer} title={`Watch ${dot.name}'s browser`}>
            <Monitor size={16} /> Computer
          </button>
        )}
        {threadId && (
          <>
            <button className="button ghost" onClick={() => onRoutine(threadId)} title="Run this conversation on a schedule">
              <Clock size={16} /> Schedule
            </button>
            <button
              className="button ghost danger"
              aria-label="Delete conversation"
              onClick={async () => {
                if (!window.confirm('Delete this conversation?')) return;
                await api(`/threads/${threadId}`, 'DELETE');
                onThread(undefined);
                onRefresh();
              }}
            >
              <Trash2 size={16} />
            </button>
          </>
        )}
      </div>
      <div className="chat-scroll" ref={scroller}>
        <div className="chat-column">
          {!threadId && (
            <div className="empty">
              <h2>What should {dot.name} take on?</h2>
              <p>{dot.instructions.split('\n')[0]}</p>
              <div className="row" style={{ justifyContent: 'center', marginTop: 16 }}>
                {starters.map((text) => (
                  <button key={text} className="button" onClick={() => setDraft(text)}>
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) => {
            if (message.role === 'tool' || message.role === 'system') return null;
            if (message.role === 'user' && message.source && !(message.source in VIA) && message.source !== 'owner')
              return (
                <details className="event" key={message.id ?? index}>
                  <summary>{EVENT_LABELS[message.source] ?? 'Background event'}</summary>
                  <Markdown text={message.text} />
                </details>
              );
            if (message.role === 'user')
              return (
                <div className="msg user" key={message.id ?? index}>
                  <div className="bubble">{message.text}</div>
                  {message.source && VIA[message.source] && <span className="via">{VIA[message.source]}</span>}
                </div>
              );
            const previous = messages[index - 1];
            const continued = previous && (previous.role === 'tool' || previous.role === 'assistant');
            return (
              <div className="msg" key={message.id ?? index}>
                {continued ? <span /> : <DotMark dot={dot} />}
                <div>
                  {!continued && <div className="who">{dot.name}</div>}
                  {!!message.tool_calls?.length && <Steps message={message} results={results} dots={state.dots} />}
                  {message.text && <Markdown text={message.text} />}
                </div>
              </div>
            );
          })}
          {live && (
            <div className="msg" aria-live="polite">
              <DotMark dot={dot} />
              <div>
                <div className="who">{dot.name}</div>
                <div className="steps">
                  {live.steps.map((step, i) => (
                    <div className={`step ${step.ok === false ? 'failed' : ''}`} key={`${String(step.id)}-${i}`}>
                      {step.type === 'tool.finished' ? <CircleCheck size={14} /> : <CircleDot size={14} />}
                      <span>{String(step.summary ?? step.name)}</span>
                    </div>
                  ))}
                </div>
                {live.text ? (
                  <Markdown text={live.text} />
                ) : (
                  <span className="typing" aria-label={`${dot.name} is working`}>
                    <i />
                    <i />
                    <i />
                  </span>
                )}
              </div>
            </div>
          )}
          {pending.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              dot={state.dots.find((d) => d.id === approval.dot_id)}
              onDecided={() => {
                setLive({ text: '', steps: [] });
                void load();
                onRefresh();
              }}
            />
          ))}
          {error && <p className="error">{error}</p>}
        </div>
      </div>
      <div className="composer">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(draft);
          }}
        >
          <textarea
            aria-label={`Message ${dot.name}`}
            placeholder={
              state.setup.missing.length
                ? `Add ${state.setup.missing.join(', ')} to .env to start`
                : pending.length
                  ? 'Decide the approval above first'
                  : `Message ${dot.name}`
            }
            value={draft}
            rows={1}
            maxLength={20000}
            disabled={!!state.setup.missing.length || !!pending.length || state.flags.paused}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!running && !busy) void send(draft);
              }
            }}
          />
          {running && threadId ? (
            <button
              type="button"
              className="button"
              onClick={() => void api(`/threads/${threadId}/stop`, 'POST').then(load)}
            >
              <Square size={14} /> Stop
            </button>
          ) : (
            <button className="button primary" aria-label="Send" disabled={busy || !draft.trim()}>
              <ArrowUp size={18} />
            </button>
          )}
        </form>
      </div>
    </section>
  );
}
