import { Pause, Play, Radio } from 'lucide-react';
import { useOfficeStore } from '../store/officeStore';

const SPEEDS = [0.5, 1, 2];

export function TopBar() {
  const tick = useOfficeStore((s) => s.tick);
  void tick;
  const { playing, speed, sourceMode } = useOfficeStore.getState();
  const setPlaying = useOfficeStore((s) => s.setPlaying);
  const setSpeed = useOfficeStore((s) => s.setSpeed);
  const connect = useOfficeStore((s) => s.connect);

  return (
    <div className="office-topbar">
      <span className={`office-conn ${sourceMode === 'live' ? 'on' : ''}`}>
        <Radio size={13} />
        {sourceMode === 'live' ? 'Live: your team right now' : 'Demo: a sample day'}
      </span>
      <div className="office-speed" role="group" aria-label="What to show">
        <button className={sourceMode === 'live' ? 'active' : ''} onClick={() => connect('live')}>
          Live
        </button>
        <button className={sourceMode === 'demo' ? 'active' : ''} onClick={() => connect('demo')}>
          Demo
        </button>
      </div>
      {sourceMode === 'demo' && (
        <>
          <button className="button ghost" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause demo' : 'Play demo'}>
            {playing ? <Pause size={15} /> : <Play size={15} />}
          </button>
          <div className="office-speed" role="group" aria-label="Demo speed">
            {SPEEDS.map((s) => (
              <button key={s} className={speed === s ? 'active' : ''} onClick={() => setSpeed(s)}>
                {s}x
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
