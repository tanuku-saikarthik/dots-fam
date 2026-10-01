import { Pause, Play } from 'lucide-react';
import type { AppState, Dot } from '../api';
import { DotMark } from './DotMark';

export function TeamLine({
  state,
  selected,
  onSelect,
  onPause,
  menu,
}: {
  state: AppState;
  selected?: string;
  onSelect: (dot: Dot) => void;
  onPause: () => void;
  menu: React.ReactNode;
}) {
  const working = new Set(state.working_dots);
  const waiting = new Map(Object.entries(state.waiting_by_dot));
  return (
    <header className="team-line" aria-label="Your team">
      {menu}
      {state.dots.map((dot) => {
        const count = waiting.get(dot.id);
        return (
          <button
            key={dot.id}
            className={`team-member c-${dot.color} ${working.has(dot.id) ? 'working' : ''} ${count ? 'waiting' : ''}`}
            aria-pressed={selected === dot.id}
            onClick={() => onSelect(dot)}
            title={`${dot.name}${dot.title ? `, ${dot.title}` : ''}${working.has(dot.id) ? ' (working)' : ''}`}
          >
            <span className="ring" data-count={count ?? ''}>
              <DotMark dot={dot} size="l" />
            </span>
            <span className="name">{dot.name}</span>
            <span className="role">{dot.title || (dot.can_delegate ? 'Chief of Staff' : 'Specialist')}</span>
          </button>
        );
      })}
      <div className="team-line-end">
        <button className="button" onClick={onPause} aria-pressed={state.flags.paused}>
          {state.flags.paused ? <Play size={16} /> : <Pause size={16} />}
          {state.flags.paused ? 'Resume team' : 'Pause team'}
        </button>
      </div>
    </header>
  );
}
