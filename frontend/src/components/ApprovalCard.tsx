import { useEffect, useState } from 'react';
import { Check, X } from 'lucide-react';
import { api, apiBlob, relative, type Approval, type Dot } from '../api';
import { DotMark } from './DotMark';

const TOOL_LABELS: Record<string, string> = {
  send_email: 'Send an email',
  slack_post: 'Post to Slack',
  computer_click: 'Click in the browser',
  computer_type: 'Submit a form in the browser',
  computer_press: 'Press a key in the browser',
  computer_shell: 'Run a shell command',
};

// Internal pointers into a snapshot: meaningless to a person, so the screen is shown instead.
const HIDDEN_ARGS = new Set(['ref', 'snapshot_id']);

/** What the Dot's browser shows while it waits: exactly the page the action would act on. */
function ScreenPeek({ dotId, name }: { dotId: string; name: string }) {
  const [src, setSrc] = useState<string>();
  useEffect(() => {
    let url: string | undefined;
    let cancelled = false;
    void apiBlob(`/computers/${dotId}/screen`).then((blob) => {
      if (!blob || cancelled) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [dotId]);
  if (!src) return null;
  return (
    <a className="peek" href={`#/computer/${dotId}`} title={`Open ${name}'s computer`}>
      <img src={src} alt={`What ${name}'s browser shows while it waits for you`} />
      <span className="muted small">What {name}'s browser shows right now. Open the computer to look closer or take control.</span>
    </a>
  );
}

export function ApprovalCard({
  approval,
  dot,
  onDecided,
}: {
  approval: Approval;
  dot?: Dot;
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const pending = approval.status === 'pending';
  const args = Object.entries(approval.args).filter(([key]) => !HIDDEN_ARGS.has(key));
  const decide = async (decision: 'approved' | 'declined') => {
    setBusy(true);
    setError('');
    try {
      await api(`/approvals/${approval.id}`, 'POST', { decision, note: note.trim() || null });
      onDecided();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the decision.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className={`approval ${pending ? '' : 'decided'}`}>
      <div className="row">
        <DotMark dot={dot} size="s" />
        <h3 className="grow">
          {dot?.name ?? 'A Dot'} wants to {(TOOL_LABELS[approval.tool] ?? approval.tool).toLowerCase()}
        </h3>
        <span className={`status ${approval.status}`}>{pending ? 'Waiting for you' : approval.status}</span>
      </div>
      <p className="reason">This {approval.reason}.</p>
      {pending && approval.tool.startsWith('computer_') && approval.tool !== 'computer_shell' && (
        <ScreenPeek dotId={approval.dot_id} name={dot?.name ?? 'the Dot'} />
      )}
      {args.length > 0 && (
        <dl className="args">
          {args.map(([key, value]) => (
            <div key={key}>
              <dt>{key.replaceAll('_', ' ')}</dt>
              <dd>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</dd>
            </div>
          ))}
        </dl>
      )}
      {pending ? (
        <>
          <input
            aria-label="Note for the Dot (optional)"
            placeholder="Note for the Dot (optional)"
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="row">
            <button className="button approve" disabled={busy} onClick={() => void decide('approved')}>
              <Check size={16} /> Approve
            </button>
            <button className="button" disabled={busy} onClick={() => void decide('declined')}>
              <X size={16} /> Decline
            </button>
            <span className="muted small">Asked {relative(approval.created_at)}. Only this exact action runs.</span>
          </div>
        </>
      ) : (
        approval.note && <p className="muted small">Note: {approval.note}</p>
      )}
      {error && <p className="error">{error}</p>}
    </article>
  );
}
