import { Pause, Play, Radio } from 'lucide-react';
import { useOfficeStore } from '../store/officeStore';

const SPEEDS = [0.5, 1, 2];

export function TopBar() {
  const tick = useOfficeStore((s) => s.tick);
  void tick;
  const { playing, speed, sourceMode, connectionStatus } = useOfficeStore.getState();
  const setPlaying = useOfficeStore((s) => s.setPlaying);
  const setSpeed = useOfficeStore((s) => s.setSpeed);
  const connect = useOfficeStore((s) => s.connect);

  return (
    <div className="office-topbar">
      <div className="office-topbar-left">
        <button className="button ghost" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? <Pause size={15} /> : <Play size={15} />}
          {playing ? 'Pause' : 'Play'}
        </button>
        <div className="office-speed" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => (
            <button key={s} className={speed === s ? 'active' : ''} onClick={() => setSpeed(s)}>
              {s}x
            </button>
          ))}
        </div>
      </div>
      <div className="office-topbar-right">
        <div className="office-speed" role="group" aria-label="Data source">
          <button className={sourceMode === 'live' ? 'active' : ''} onClick={() => connect('live')}>
            Live
          </button>
          <button className={sourceMode === 'demo' ? 'active' : ''} onClick={() => connect('demo')}>
            Demo
          </button>
        </div>
        <span className={`office-conn ${connectionStatus === 'live' ? 'on' : ''}`}>
          <Radio size={13} />
          {sourceMode === 'live' ? "Watching your real team" : 'Simulated demo flow'}
        </span>
      </div>
    </div>
  );
}
