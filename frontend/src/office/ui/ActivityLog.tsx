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
      <h3>Activity</h3>
      <div className="office-log-list" ref={scroller}>
        {!log.length && <p className="muted small">Nothing yet — work will appear here as it happens.</p>}
        {log.slice(-80).map((entry) => (
          <div className="office-log-item" key={entry.id}>
            <time>{new Date(entry.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
            <span>{entry.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
