import { Suspense, useEffect, useState } from 'react';
import { api, type GraphData } from '../api';
import { OfficeCanvas } from './scene/Office';
import { TopBar } from './ui/TopBar';
import { ActivityLog } from './ui/ActivityLog';
import { AgentDetail } from './ui/AgentDetail';
import { useOfficeStore } from './store/officeStore';
import type { DotMeta } from './events/types';
import './office.css';

export function OfficeView({ family = 'office', navigate }: { family?: string; navigate?: (path: string) => void }) {
  const ready = useOfficeStore((s) => s.ready);
  const initRoster = useOfficeStore((s) => s.initRoster);
  const connect = useOfficeStore((s) => s.connect);
  const [error, setError] = useState('');
  const [families, setFamilies] = useState<GraphData['families']>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const graph = await api<GraphData>('/graph');
        if (cancelled) return;
        setFamilies(graph.families);
        const team = graph.families.find((f) => f.name === family) ?? graph.families[0];
        if (!team) {
          setError('No Dots yet — set up your team first.');
          return;
        }
        const members = team.members.map((id) => graph.nodes.find((n) => n.id === id)).filter((n) => !!n);
        const coordinator = members.find((d) => d.id === team.lead) ?? members[0];
        const workers = members.filter((d) => d.id !== coordinator.id);
        const dotMeta = Object.fromEntries(
          members.map((d): [string, DotMeta] => [d.id, { id: d.id, name: d.name, title: d.title, color: d.color }]),
        );
        initRoster(coordinator.id, workers.map((d) => d.id), dotMeta);
        connect('live', members.map((d) => d.id));
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load your team.');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initRoster, connect, family]);

  return (
    <div className="office-view">
      <div className="office-stage">
        {ready ? (
          <Suspense fallback={<div className="office-loading">Setting up the office…</div>}>
            <OfficeCanvas />
          </Suspense>
        ) : (
          <div className="office-loading">{error || 'Loading your team…'}</div>
        )}
        {families.length > 1 && (
          <nav className="office-teams" aria-label="Teams">
            {families.map((f) => (
              <button
                key={f.name}
                className={f.name === family ? 'on' : ''}
                onClick={() => navigate?.(`/office/${f.name}`)}
              >
                {f.title}
              </button>
            ))}
            <button onClick={() => navigate?.('/graph')}>Graph</button>
          </nav>
        )}
        <TopBar />
        <ActivityLog />
        <AgentDetail />
      </div>
    </div>
  );
}
