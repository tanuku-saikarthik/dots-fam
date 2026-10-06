import { useEffect, useState } from 'react';
import { Phone, PhoneOff } from 'lucide-react';
import { api, type Dot, type Thread } from '../api';
import { DotMark } from '../components/DotMark';

interface Incoming {
  dot: Dot;
  thread: Thread;
  waiting: string[];
}

/** Full-screen "<Dot> is calling", opened from the push / ntfy alert. Answer starts the voice call. */
export function IncomingCall({
  threadId,
  onAnswer,
  onDecline,
}: {
  threadId: string;
  onAnswer: (dotId: string, threadId: string) => void;
  onDecline: () => void;
}) {
  const [call, setCall] = useState<Incoming>();
  const [error, setError] = useState('');

  useEffect(() => {
    let stop = false;
    const load = () =>
      api<Incoming>(`/calls/${threadId}`)
        .then((next) => !stop && setCall(next))
        .catch((e) => !stop && setError(e instanceof Error ? e.message : 'This call ended.'));
    void load();
    const timer = setInterval(load, 4000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [threadId]);

  useEffect(() => {
    document.title = call ? `${call.dot.name} is calling` : 'Incoming call';
    return () => {
      document.title = 'Dots Fam';
    };
  }, [call]);

  if (error)
    return (
      <main className="incoming">
        <div className="incoming-who">
          <h1>Missed call</h1>
          <p>{error}</p>
        </div>
        <div className="incoming-actions single">
          <button className="incoming-btn" onClick={onDecline}>
            Open Dots Fam
          </button>
        </div>
      </main>
    );
  if (!call) return <main className="incoming" aria-busy="true" />;

  const { dot, waiting } = call;
  const settled = waiting.length === 0;
  return (
    <main className={`incoming c-${dot.color ?? 'blue'}`}>
      <div className="incoming-who">
        <div className={`incoming-orb${settled ? ' still' : ''}`} aria-hidden="true">
          <i />
          <i />
          <i />
          <DotMark dot={dot} size="xl" />
        </div>
        <h1>{dot.name}</h1>
        <p>{settled ? 'Nothing is waiting anymore' : 'is calling'}</p>
      </div>

      {!settled && (
        <section className="incoming-why" aria-label="What needs your OK">
          {waiting.slice(0, 3).map((line) => (
            <p key={line}>{line.replace(/ Approve or decline\?$/, '')}</p>
          ))}
          {waiting.length > 3 && <p className="more">and {waiting.length - 3} more</p>}
        </section>
      )}

      <div className="incoming-actions">
        <button className="incoming-btn decline" onClick={onDecline} aria-label="Decline">
          <PhoneOff size={28} />
          <span>{settled ? 'Close' : 'Later'}</span>
        </button>
        <button className="incoming-btn answer" onClick={() => onAnswer(dot.id, threadId)} aria-label={`Answer ${dot.name}`}>
          <Phone size={28} />
          <span>Answer</span>
        </button>
      </div>
    </main>
  );
}
