import { Suspense, useEffect, useState } from 'react';
import { api, type AppState } from '../api';
import { OfficeCanvas } from './scene/Office';
import { TopBar } from './ui/TopBar';
import { Roster } from './ui/Roster';
import { ActivityLog } from './ui/ActivityLog';
import { AgentDetail } from './ui/AgentDetail';
import { useOfficeStore } from './store/officeStore';
import type { DotMeta } from './events/types';
import './office.css';

export function OfficeView() {
  const ready = useOfficeStore((s) => s.ready);
  const initRoster = useOfficeStore((s) => s.initRoster);
  const connect = useOfficeStore((s) => s.connect);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const state = await api<AppState>('/state');
        if (cancelled) return;
        if (!state.dots.length) {
          setError('No Dots yet — set up your team first.');
          return;
        }
        const coordinator = state.dots.find((d) => d.can_delegate) ?? state.dots[0];
        const workers = state.dots.filter((d) => d.id !== coordinator.id);
        const dotMeta = Object.fromEntries(
          state.dots.map((d): [string, DotMeta] => [d.id, { id: d.id, name: d.name, title: d.title, color: d.color }]),
        );
        initRoster(coordinator.id, workers.map((d) => d.id), dotMeta);
        connect('live');
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load your team.');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initRoster, connect]);

  return (
    <div className="office-view">
      <TopBar />
      <div className="office-body">
        <Roster />
        <div className="office-canvas-wrap">
          {ready ? (
            <Suspense fallback={<div className="office-loading">Setting up the office…</div>}>
              <OfficeCanvas />
            </Suspense>
          ) : (
            <div className="office-loading">{error || 'Loading your team…'}</div>
          )}
          <AgentDetail />
        </div>
        <ActivityLog />
      </div>
    </div>
  );
}
