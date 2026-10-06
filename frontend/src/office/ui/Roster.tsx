import { useOfficeStore } from '../store/officeStore';
import { dotColorHex } from '../events/types';

const PHASE_LABEL: Record<string, string> = {
  idle: 'Idle',
  working: 'Working',
  standing_up: 'Standing up',
  walking: 'Walking',
  walking_back: 'Walking back',
  handing_off: 'Handing off',
  sitting_down: 'Sitting down',
  waiting: 'Waiting',
};

export function Roster() {
  const tick = useOfficeStore((s) => s.tick);
  void tick;
  const { agents, tasks, dotMeta, workerIds, selectedAgent } = useOfficeStore.getState();
  const select = useOfficeStore((s) => s.selectAgent);

  return (
    <div className="office-panel office-roster">
      <h3>Team</h3>
      <div className="office-roster-list">
        {workerIds.map((id) => {
          const agent = agents[id];
          if (!agent) return null;
          const task = agent.currentTaskId ? tasks[agent.currentTaskId] : null;
          return (
            <button
              key={id}
              className={`office-roster-item ${selectedAgent === id ? 'selected' : ''}`}
              onClick={() => select(selectedAgent === id ? null : id)}
            >
              <span className="office-roster-dot" style={{ background: dotColorHex(dotMeta[id]?.color ?? 'blue') }} />
              <span className="office-roster-main">
                <span className="office-roster-name">{dotMeta[id]?.name ?? id}</span>
                <span className="office-roster-task">{task ? task.title : agent.waitingReason || dotMeta[id]?.title || '—'}</span>
              </span>
              <span className={`office-status-chip phase-${agent.phase}`}>
                {agent.waitingReason && agent.phase === 'waiting' ? 'Waiting' : PHASE_LABEL[agent.phase]}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
