import type { ComputerAction } from '../shared/computer-types.js';

/**
 * The Reversibility Law, enforced at the tool boundary:
 * a Dot may read, research, analyze and draft on its own, but any step that
 * changes the outside world needs an owner approval first.
 *
 * Browser steps are classified from the element the Dot is acting on, using
 * the accessible names from its latest snapshot. This is a best-effort
 * classifier, not a sandbox: keep shell access off unless a Dot needs it.
 */
const commitWords =
  /\b(send|submit|post|publish|reply|tweet|share|invite|pay|purchase|buy|order|checkout|check out|subscribe|unsubscribe|delete|remove|archive|confirm|transfer|withdraw|merge|deploy|approve|book|save changes|update)\b/i;
const searchLike = /\b(search|find|query|filter|lookup|look up)\b/i;
const riskyShell =
  /\b(git\s+push|gh\s+(pr|issue|release|repo|api)\b(?!\s+(list|view|status|diff|checks))|npm\s+publish|yarn\s+publish|pnpm\s+publish|twine\s+upload|docker\s+push|kubectl\s+(apply|delete|scale|rollout)|terraform\s+(apply|destroy)|sendmail|mailx?\s|ssh\s|scp\s|rsync\s.*:|aws\s+\S+\s+(create|delete|put|update|terminate)|curl\b[^|]*\s-X\s*(POST|PUT|PATCH|DELETE)|curl\b[^|]*\s(-d|--data[\w-]*|-F|--form)\b|wget\b[^|]*--post|http(ie)?\s+(POST|PUT|PATCH|DELETE)|rm\s+-[a-z]*r[a-z]*f?\s+\/)/i;

interface Element {
  role: string;
  name: string;
}
interface Snapshot {
  snapshotId: number;
  elements: Map<string, Element>;
}
const snapshots = new Map<string, Snapshot>();
const lastTyped = new Map<string, Element>();

/** Remember the element names from a Dot's latest browser snapshot. */
export function rememberSnapshot(dotId: string, result: unknown) {
  if (!result || typeof result !== 'object') return;
  const value = result as { snapshotId?: unknown; elements?: unknown };
  if (typeof value.snapshotId !== 'number' || !Array.isArray(value.elements))
    return;
  const elements = new Map<string, Element>();
  for (const item of value.elements) {
    if (
      item &&
      typeof item === 'object' &&
      typeof (item as { ref?: unknown }).ref === 'string'
    ) {
      const element = item as { ref: string; role?: unknown; name?: unknown };
      elements.set(element.ref, {
        role: typeof element.role === 'string' ? element.role : '',
        name: typeof element.name === 'string' ? element.name : '',
      });
    }
  }
  snapshots.set(dotId, { snapshotId: value.snapshotId, elements });
}

export function forgetComputerState(dotId?: string) {
  if (dotId) {
    snapshots.delete(dotId);
    lastTyped.delete(dotId);
  } else {
    snapshots.clear();
    lastTyped.clear();
  }
}

function element(dotId: string, input: Record<string, unknown>): Element {
  const snapshot = snapshots.get(dotId);
  const ref = typeof input.ref === 'string' ? input.ref : '';
  const found =
    snapshot && snapshot.snapshotId === input.snapshotId
      ? snapshot.elements.get(ref)
      : undefined;
  if (!found)
    throw new Error(
      'Take a fresh computer_snapshot and act on refs from it; this element could not be checked against the approval rules.',
    );
  return found;
}

const describe = (item: Element) =>
  `${item.role || 'element'} "${item.name.slice(0, 80)}"`;

/**
 * Returns a description of the outside-world change a computer step would
 * make, or undefined if the step is reversible (reading, navigating, typing
 * a draft, searching).
 */
export function gatedComputerStep(
  dotId: string,
  action: ComputerAction,
  input: Record<string, unknown>,
): string | undefined {
  if (action === 'click') {
    const target = element(dotId, input);
    return commitWords.test(target.name)
      ? `click ${describe(target)}`
      : undefined;
  }
  if (action === 'type') {
    const target = element(dotId, input);
    lastTyped.set(dotId, target);
    if (!input.submit) return undefined;
    return target.role === 'searchbox' || searchLike.test(target.name)
      ? undefined
      : `submit ${describe(target)}`;
  }
  if (action === 'key') {
    const key = typeof input.key === 'string' ? input.key : '';
    // Enter, Return, and chords such as Control+Enter or Meta+Enter (send shortcuts).
    if (!/(^|\+)(enter|return|numpadenter)$/i.test(key)) return undefined;
    const target = lastTyped.get(dotId);
    if (target && (target.role === 'searchbox' || searchLike.test(target.name)))
      return undefined;
    return target ? `press Enter in ${describe(target)}` : 'press Enter';
  }
  if (action === 'exec') {
    const command = typeof input.command === 'string' ? input.command : '';
    return riskyShell.test(command)
      ? 'run a shell command that changes an outside system'
      : undefined;
  }
  return undefined;
}

export const reversibilityPrompt = `Reversibility Law (enforced): you may read, research, analyze, and draft with full autonomy. Any action that alters external state, spends money, sends or publishes communications, or overwrites or deletes records needs explicit owner sign-off first. To get it, call request_approval with a one-line summary and the exact details, then stop and tell the owner what is waiting. When an approval is granted you will be told its approval_id; pass that approval_id to the gated tool call. Never try to work around a refused step.`;
