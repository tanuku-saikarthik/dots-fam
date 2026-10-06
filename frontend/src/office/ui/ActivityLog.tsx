import { useEffect, useRef } from 'react';
import { useOfficeStore } from '../store/officeStore';

export function ActivityLog() {
  const tick = useOfficeStore((s) => s.tick);
  void tick;
  const { log } = useOfficeStore.getState();
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [log.length]);

  return (
    <div className="office-panel office-log">
      <h3>Happening now</h3>
      <div className="office-log-list" ref={scroller}>
        {!log.length && <p className="muted small">Quiet for now. Ask Vance for something and watch the handoffs here.</p>}
        {log.slice(-4).map((entry) => (
          <div className="office-log-item" key={entry.id}>
            <time>{new Date(entry.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
            <span>{entry.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
