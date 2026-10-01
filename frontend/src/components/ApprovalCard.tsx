import { useState } from 'react';
import { Check, X } from 'lucide-react';
import { api, relative, type Approval, type Dot } from '../api';
import { DotMark } from './DotMark';

const TOOL_LABELS: Record<string, string> = {
  send_email: 'Send an email',
  slack_post: 'Post to Slack',
  computer_click: 'Click in the browser',
  computer_type: 'Submit in the browser',
  computer_key: 'Press a key in the browser',
  computer_exec: 'Run a shell command',
};

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
      <dl className="args">
        {Object.entries(approval.args).map(([key, value]) => (
          <div key={key}>
            <dt>{key.replaceAll('_', ' ')}</dt>
            <dd>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</dd>
          </div>
        ))}
      </dl>
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
