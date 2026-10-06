import { X } from 'lucide-react';
import { useOfficeStore } from '../store/officeStore';
import { dotColorHex } from '../events/types';

export function AgentDetail() {
  const tick = useOfficeStore((s) => s.tick);
  void tick;
  const { selectedAgent, agents, tasks, dotMeta } = useOfficeStore.getState();
  const select = useOfficeStore((s) => s.selectAgent);
  if (!selectedAgent) return null;

  const agent = agents[selectedAgent];
  if (!agent) return null;
  const history = Object.values(tasks)
    .filter((t) => t.history.includes(selectedAgent))
    .sort((a, b) => (a.done === b.done ? 0 : a.done ? 1 : -1));

  return (
    <div className="office-panel office-detail">
      <div className="office-detail-head">
        <span className="office-roster-dot" style={{ background: dotColorHex(dotMeta[selectedAgent]?.color ?? 'blue') }} />
        <h3>{dotMeta[selectedAgent]?.name ?? selectedAgent}</h3>
        <button className="button ghost" aria-label="Close" onClick={() => select(null)}>
          <X size={15} />
        </button>
      </div>
      <p className="muted small">
        {agent.currentTaskId ? tasks[agent.currentTaskId]?.title : dotMeta[selectedAgent]?.title || 'No active task'}
      </p>
      <div className="office-detail-history">
        {!history.length && <p className="muted small">No task history yet.</p>}
        {history.map((task) => (
          <div className="office-detail-task" key={task.id}>
            <span className={task.done ? 'done' : ''}>{task.title}</span>
            <small>{task.done ? 'Done' : `${Math.round(task.progress * 100)}%`}</small>
          </div>
        ))}
      </div>
    </div>
  );
}
