import { Pause, Play } from 'lucide-react';
import { motion } from 'framer-motion';
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
        const isWorking = working.has(dot.id);
        const isSelected = selected === dot.id;
        return (
          <motion.button
            key={dot.id}
            className={`team-member c-${dot.color} ${isWorking ? 'working' : ''} ${count ? 'waiting' : ''}`}
            aria-pressed={isSelected}
            onClick={() => onSelect(dot)}
            title={`${dot.name}${dot.title ? `, ${dot.title}` : ''}${isWorking ? ' (working)' : ''}`}
            whileHover={{ y: -3 }}
            whileTap={{ scale: 0.95 }}
            transition={{ type: 'spring', stiffness: 420, damping: 22 }}
          >
            {isSelected && (
              <motion.span
                layoutId="team-active"
                className="team-active"
                transition={{ type: 'spring', stiffness: 500, damping: 40 }}
              />
            )}
            <span className="ring" data-count={count ?? ''}>
              <DotMark dot={dot} size="l" active={isWorking} />
            </span>
            <span className="name">{dot.name}</span>
            <span className="role">{dot.title || (dot.can_delegate ? 'Chief of Staff' : 'Specialist')}</span>
          </motion.button>
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
