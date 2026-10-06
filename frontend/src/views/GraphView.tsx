import { useEffect, useMemo, useState } from 'react';
import { api, type GraphData, type GraphEdge, type GraphFamily, type GraphNode } from '../api';
import { DotMark } from '../components/DotMark';

const W = 1000;
const NODE_W = 160;
const NODE_H = 54;
const GAP = 52; // space between family bands
const HEAD = 66; // band header

interface Placed {
  node: GraphNode;
  x: number; // centre
  y: number;
}
interface Band {
  family: GraphFamily;
  top: number;
  height: number;
  placed: Placed[];
}

/** Pipeline order: follow the declared flow from the lead; anything left over goes last. */
function pipelineOrder(family: GraphFamily, nodes: Map<string, GraphNode>): GraphNode[] {
  const order: string[] = [];
  let current = family.lead ?? family.members[0];
  while (current && !order.includes(current)) {
    order.push(current);
    const next = family.flow.find((e) => e.from === current && e.kind !== 'loop' && !order.includes(e.to));
    current = next?.to ?? '';
  }
  for (const id of family.members) if (!order.includes(id)) order.push(id);
  return order.map((id) => nodes.get(id)).filter((n): n is GraphNode => !!n);
}

function layout(data: GraphData): Band[] {
  const nodes = new Map(data.nodes.map((n) => [n.id, n]));
  let top = 0;
  return data.families.map((family) => {
    const members = family.members.map((id) => nodes.get(id)).filter((n): n is GraphNode => !!n);
    let placed: Placed[];
    let height: number;
    if (family.kind === 'harness' || family.kind === 'pipeline') {
      const ordered = pipelineOrder(family, nodes);
      const span = W - 2 * (NODE_W / 2 + 30);
      const step = ordered.length > 1 ? span / (ordered.length - 1) : 0;
      placed = ordered.map((node, i) => ({ node, x: NODE_W / 2 + 30 + i * step, y: top + HEAD + 40 }));
      height = HEAD + 40 + 80;
    } else {
      const lead = members.find((m) => m.id === family.lead) ?? members[0];
      const rest = members.filter((m) => m.id !== lead?.id);
      const rows = Math.max(rest.length, 1);
      height = HEAD + 6 + rows * 60;
      const mid = top + HEAD + 6 + (rows * 60) / 2;
      placed = [
        ...(lead ? [{ node: lead, x: 150, y: mid }] : []),
        ...rest.map((node, i) => ({ node, x: 560, y: top + HEAD + 6 + i * 60 + 30 })),
      ];
    }
    const band = { family, top, height, placed };
    top += height + GAP;
    return band;
  });
}

const EDGE_COLOR: Record<GraphEdge['kind'] | 'peer' | 'handoff', string> = {
  flow: 'var(--ink)',
  pass: '#0e9f8e',
  loop: '#f26a21',
  peer: '#7a4dff',
  handoff: '#2c6bed',
};

function link(a: Placed, b: Placed, bend = 0): { d: string; mx: number; my: number } {
  const horizontal = Math.abs(b.x - a.x) > 1;
  if (horizontal) {
    const dir = b.x > a.x ? 1 : -1;
    const x1 = a.x + (dir * NODE_W) / 2;
    const x2 = b.x - (dir * NODE_W) / 2;
    const c = (x2 - x1) / 2;
    return {
      d: `M${x1},${a.y} C${x1 + c},${a.y + bend} ${x2 - c},${b.y + bend} ${x2},${b.y}`,
      mx: (x1 + x2) / 2,
      my: (a.y + b.y) / 2 + bend * 0.75,
    };
  }
  const dir = b.y > a.y ? 1 : -1;
  const y1 = a.y + (dir * NODE_H) / 2;
  const y2 = b.y - (dir * NODE_H) / 2;
  const off = 70 + bend;
  return { d: `M${a.x + NODE_W / 2},${a.y} C${a.x + NODE_W / 2 + off},${a.y} ${b.x + NODE_W / 2 + off},${b.y} ${b.x + NODE_W / 2},${b.y}`, mx: a.x + NODE_W / 2 + off * 0.75, my: (y1 + y2) / 2 };
}

export function GraphView({ navigate }: { navigate: (path: string) => void }) {
  const [data, setData] = useState<GraphData>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const next = await api<GraphData>('/graph');
        if (!stop) setData(next);
      } catch (e) {
        if (!stop) setError(e instanceof Error ? e.message : 'Could not load the graph.');
      }
    };
    void load();
    const timer = setInterval(() => void load(), 1800);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, []);

  const bands = useMemo(() => (data ? layout(data) : []), [data]);
  const byId = useMemo(() => {
    const map = new Map<string, Placed>();
    for (const band of bands) for (const p of band.placed) map.set(p.node.id, p);
    return map;
  }, [bands]);

  if (!data) return <p className="muted graph-empty">{error || 'Loading the team graph…'}</p>;
  if (!data.nodes.length)
    return (
      <div className="empty">
        <h2>No Dots yet</h2>
        <button className="button primary" onClick={() => navigate('/team')}>
          Set up your team
        </button>
      </div>
    );

  const hasHarness = data.families.some((f) => f.kind === 'harness');
  const height = bands.length ? bands[bands.length - 1].top + bands[bands.length - 1].height + 20 : 200;
  const declared = new Set(data.families.flatMap((f) => f.flow.map((e) => `${e.from}>${e.to}`)));
  const liveBetween = (from: string, to: string) =>
    data.live.filter((l) => l.from === from && l.to === to);
  const leads = data.families.map((f) => f.lead).filter((id): id is string => !!id);
  const teamOf = new Map(data.nodes.map((n) => [n.id, n.family]));
  const sameTeam = (a: string, b: string) => teamOf.get(a) === teamOf.get(b);
  const officeLead = data.families.find((f) => f.name === 'office')?.lead;

  return (
    <div className="graph-view">
      <header className="graph-head">
        <div>
          <h1>Team graph</h1>
          <p className="muted">
            Who works with whom, and what is moving right now. Each team has its own structure.
          </p>
        </div>
        <div className="graph-actions">
          {!hasHarness && (
            <button
              className="button primary"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void api('/team/install-harness', 'POST', {}).finally(() => setBusy(false));
              }}
            >
              Add the build harness
            </button>
          )}
          <button className="button" onClick={() => navigate('/office')}>
            Open the 3D office
          </button>
        </div>
      </header>

      <div className="graph-legend" aria-label="Legend">
        <span><i className="lg lg-flow" /> hands work on</span>
        <span><i className="lg lg-pass" /> verified, moves on</span>
        <span><i className="lg lg-loop" /> failed, goes back</span>
        <span><i className="lg lg-peer" /> teammates talking</span>
        <span><i className="lg lg-live" /> in motion now</span>
      </div>

      <div className="graph-scroll">
        <svg
          className="graph-svg"
          viewBox={`0 0 ${W} ${height}`}
          role="img"
          aria-label="Graph of Dot teams and how work moves between them"
        >
          <defs>
            {(Object.keys(EDGE_COLOR) as (keyof typeof EDGE_COLOR)[]).map((k) => (
              <marker key={k} id={`arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill={EDGE_COLOR[k]} />
              </marker>
            ))}
          </defs>

          {bands.map((band) => (
            <g key={band.family.name} data-family={band.family.name}>
              <rect
                className={`band band-${band.family.kind}`}
                x={4}
                y={band.top}
                width={W - 8}
                height={band.height}
                rx={18}
              />
              <foreignObject x={190} y={band.top + 12} width={W - 400} height={HEAD - 8}>
                <div className="band-title">
                  <strong>{band.family.title}</strong>
                  <span>{band.family.summary}</span>
                </div>
              </foreignObject>
              <foreignObject x={W - 190} y={band.top + 16} width={170} height={36}>
                <button
                  className="button ghost band-office"
                  onClick={() => navigate(`/office/${band.family.name}`)}
                >
                  See it in the office
                </button>
              </foreignObject>

              {band.family.flow.map((edge, i) => {
                const a = byId.get(edge.from);
                const b = byId.get(edge.to);
                if (!a || !b) return null;
                const loop = edge.kind === 'loop';
                const { d, mx, my } = link(a, b, loop ? 90 : 0);
                const received = data.live.filter((l) => l.to === edge.to).reduce((n, l) => n + l.count, 0);
                const active = loop
                  ? b.node.state === 'working' && received > 1 // the builder is back at it after a failed check
                  : b.node.state === 'working' || liveBetween(edge.from, edge.to).some((l) => l.running);
                return (
                  <g key={`${edge.from}-${edge.to}-${i}`} className={`edge edge-${edge.kind}${active ? ' active' : ''}`}>
                    <path
                      d={loop ? loopPath(a, b) : d}
                      stroke={EDGE_COLOR[edge.kind]}
                      markerEnd={`url(#arrow-${edge.kind})`}
                    />
                    {edge.label && (
                      <text x={loop ? (a.x + b.x) / 2 : mx} y={loop ? a.y + 78 : my - 10} textAnchor="middle" fill={EDGE_COLOR[edge.kind]}>
                        {edge.label}
                      </text>
                    )}
                  </g>
                );
              })}

              {band.placed.map(({ node, x, y }) => (
                <foreignObject key={node.id} x={x - NODE_W / 2} y={y - NODE_H / 2} width={NODE_W} height={NODE_H}>
                  <button
                    className={`gnode state-${node.state}${node.lead ? ' lead' : ''}`}
                    onClick={() => navigate(`/chat/${node.id}`)}
                    aria-label={`${node.name}, ${node.title}, ${node.state}`}
                  >
                    <DotMark dot={node} size="m" active={node.state === 'working'} />
                    <span className="gnode-text">
                      <strong>{node.name}</strong>
                      <small>{node.state === 'idle' ? node.title : node.state === 'waiting' ? 'waiting on you' : 'working'}</small>
                    </span>
                  </button>
                </foreignObject>
              ))}
            </g>
          ))}

          {/* Work handed from the office lead to another team's lead */}
          {officeLead &&
            leads
              .filter((id) => id !== officeLead)
              .map((id) => {
                const a = byId.get(officeLead);
                const b = byId.get(id);
                if (!a || !b) return null;
                const running = liveBetween(officeLead, id).some((l) => l.running);
                const x = 40;
                return (
                  <g key={`hand-${id}`} className={`edge edge-handoff${running ? ' active' : ''}`}>
                    <path
                      d={`M${a.x - NODE_W / 2},${a.y} C${x},${a.y} ${x + 10},${b.y - 70} ${b.x - NODE_W / 2 + 40},${b.y - NODE_H / 2 - 4}`}
                      stroke={EDGE_COLOR.handoff}
                      markerEnd="url(#arrow-handoff)"
                    />
                    <text x={x + 14} y={b.y - NODE_H / 2 - 24} fill={EDGE_COLOR.handoff}>hands over a whole job</text>
                  </g>
                );
              })}

          {/* Messages that are not part of a team's declared structure (teammates asking each other) */}
          {data.live
            .filter((l) => l.kind === 'peer' || (!declared.has(`${l.from}>${l.to}`) && !sameTeam(l.from, l.to) && l.from !== officeLead))
            .map((l) => {
              const a = byId.get(l.from);
              const b = byId.get(l.to);
              if (!a || !b || a === b) return null;
              const { d, mx, my } = link(a, b, l.kind === 'peer' ? 30 : 0);
              return (
                <g key={`${l.from}-${l.to}-${l.kind}`} className={`edge edge-peer${l.running ? ' active' : ''}`}>
                  <path d={d} stroke={EDGE_COLOR.peer} markerEnd="url(#arrow-peer)" />
                  <text x={mx} y={my - 6} textAnchor="middle" fill={EDGE_COLOR.peer}>
                    {l.kind === 'peer' ? 'asked' : 'handed'} ×{l.count}
                  </text>
                </g>
              );
            })}
        </svg>
      </div>
    </div>
  );
}

/** Rework loop: out of the bottom of the verifier, back into the bottom of the builder. */
function loopPath(a: Placed, b: Placed): string {
  const y = a.y + NODE_H / 2;
  const drop = y + 40;
  return `M${a.x - 20},${y} C${a.x - 20},${drop} ${b.x + 20},${drop} ${b.x + 20},${y + 2}`;
}
