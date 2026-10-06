import { useEffect, useState } from 'react';
import { BellRing, PhoneCall } from 'lucide-react';
import { api, relative } from '../api';
import { deviceSubscription, disableCallsHere, enableCallsHere, pushSupport, type CallsStatus } from '../calls';

const SUPPORT_HELP: Record<string, string> = {
  insecure: 'Calls need HTTPS (or localhost). Open Dots Fam over your HTTPS address on this device.',
  'ios-install': 'On iPhone, add Dots Fam to your Home Screen first (Share, then Add to Home Screen), open it from there, and turn calls on.',
  unsupported: "This browser can't receive calls. Use Chrome, Edge, Firefox or Safari, or set up ntfy below.",
};

/** Settings: let the team ring this phone when it needs an OK. Free: web push and ntfy, no phone number. */
export function CallsPanel() {
  const [status, setStatus] = useState<CallsStatus>();
  const [here, setHere] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const support = pushSupport();

  useEffect(() => {
    void api<CallsStatus>('/calls')
      .then(setStatus)
      .catch(() => setStatus(undefined));
    void deviceSubscription().then((sub) => setHere(!!sub));
  }, []);

  if (!status) return null;

  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.');
    } finally {
      setBusy(false);
    }
  };
  const ready = status.push_devices > 0 || status.ntfy;
  const ntfyLink = status.ntfy_topic ? `${status.ntfy_server.replace(/\/$/, '')}/${status.ntfy_topic}` : null;

  return (
    <section className="panel stack calls-panel">
      <div>
        <h2>Calls</h2>
        <p className="muted small">
          When a Dot needs your OK, it calls your phone. Answer, hear what's waiting, and say yes or no. Free, with
          no phone number or SIM.
        </p>
      </div>

      <div className="calls-row">
        <BellRing size={18} aria-hidden="true" />
        <div className="grow">
          <strong>This device</strong>
          <p className="muted small">
            {support !== 'ok'
              ? SUPPORT_HELP[support]
              : here
                ? 'Calls ring here.'
                : 'Turn on to get calls in this browser, even when the tab is closed.'}
          </p>
        </div>
        {support === 'ok' &&
          (here ? (
            <button
              className="button ghost"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const next = await disableCallsHere();
                  if (next) setStatus({ ...status, ...next });
                  setHere(false);
                })
              }
            >
              Turn off
            </button>
          ) : (
            <button
              className="button primary"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const next = await enableCallsHere(status.public_key);
                  setStatus({ ...status, ...next });
                  setHere(true);
                })
              }
            >
              Turn on calls
            </button>
          ))}
      </div>

      <div className="calls-row">
        <PhoneCall size={18} aria-hidden="true" />
        <div className="grow">
          <strong>ntfy app</strong>
          <p className="muted small">
            {ntfyLink ? (
              <>
                Ringing the ntfy topic <code>{status.ntfy_topic}</code>. Subscribe to it in the ntfy app and allow
                urgent alerts.{' '}
                <a href={ntfyLink} target="_blank" rel="noreferrer">
                  Open topic
                </a>
              </>
            ) : (
              <>
                Optional, for the loudest alert. Install the free ntfy app, pick a hard-to-guess topic, set{' '}
                <code>NTFY_TOPIC</code> in your .env and restart.
              </>
            )}
          </p>
        </div>
      </div>

      <div className="row">
        <button
          className="button"
          disabled={busy || !ready}
          title={ready ? undefined : 'Turn on calls on a device, or set NTFY_TOPIC, first.'}
          onClick={() =>
            act(async () => {
              const sent = await api<{ push: number; ntfy: number }>('/calls/test', 'POST');
              const where = [sent.push && `${sent.push} device${sent.push > 1 ? 's' : ''}`, sent.ntfy && 'ntfy']
                .filter(Boolean)
                .join(' and ');
              setMessage(`Ringing ${where}.`);
            })
          }
        >
          Test call
        </button>
        <span className="muted small">
          {status.push_devices} device{status.push_devices === 1 ? '' : 's'} set up
          {status.recent.length ? `, last rang ${relative(status.recent[status.recent.length - 1].at)}` : ''}
        </span>
      </div>
      {message && <p className="muted small">{message}</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
