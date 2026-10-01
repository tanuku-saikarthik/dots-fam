import type { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import type { Approval, ApprovalStatus } from '../shared/types.js';

/** An approval stays usable for this long after the owner approves it. */
export const APPROVAL_WINDOW_MS = 2 * 60 * 60 * 1000;
/** A single approval may cover a few gated steps of the same action. */
export const APPROVAL_MAX_USES = 5;
const MAX_PENDING_PER_DOT = 20;

export const approvalKinds = [
  'send_message',
  'publish',
  'submit_form',
  'purchase',
  'delete',
  'modify_record',
  'run_command',
  'other',
] as const;
export const approvalRequest = z
  .object({
    action_kind: z.enum(approvalKinds),
    summary: z
      .string()
      .trim()
      .min(5)
      .max(300)
      .describe('One line the owner can approve: what, where, and to whom.'),
    details: z
      .string()
      .trim()
      .max(4000)
      .default('')
      .describe(
        'The exact message text, form values, record change, or command.',
      ),
  })
  .strict();

type Row = Omit<Approval, never> & { uses: number };

export class Approvals {
  constructor(private db: DatabaseSync) {
    db.exec(`CREATE TABLE IF NOT EXISTS approvals(
      id TEXT PRIMARY KEY, dotId TEXT NOT NULL, threadId TEXT, delegationId TEXT,
      kind TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL,
      status TEXT NOT NULL, createdAt INTEGER NOT NULL, decidedAt INTEGER,
      usedAt INTEGER, uses INTEGER NOT NULL DEFAULT 0);
      CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status, createdAt);`);
  }
  private row(id: string): Row | undefined {
    return this.db.prepare('SELECT * FROM approvals WHERE id=?').get(id) as
      Row | undefined;
  }
  private view(row: Row, now = Date.now()): Approval {
    const expired =
      (row.status === 'approved' || row.status === 'used') &&
      row.decidedAt !== null &&
      now - row.decidedAt > APPROVAL_WINDOW_MS;
    return {
      id: row.id,
      dotId: row.dotId,
      threadId: row.threadId,
      delegationId: row.delegationId,
      kind: row.kind,
      summary: row.summary,
      details: row.details,
      status: expired ? 'expired' : row.status,
      createdAt: row.createdAt,
      decidedAt: row.decidedAt,
      usedAt: row.usedAt,
    };
  }
  list(limit = 100): Approval[] {
    return (
      this.db
        .prepare(
          "SELECT * FROM approvals ORDER BY status='pending' DESC, createdAt DESC LIMIT ?",
        )
        .all(limit) as unknown as Row[]
    ).map((row) => this.view(row));
  }
  get(id: string): Approval | undefined {
    const row = this.row(id);
    return row && this.view(row);
  }
  pendingCount() {
    return Number(
      (
        this.db
          .prepare("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'")
          .get() as { n: number }
      ).n,
    );
  }
  request(input: {
    dotId: string;
    threadId: string | null;
    delegationId: string | null;
    kind: string;
    summary: string;
    details: string;
  }): Approval {
    const pending = (
      this.db
        .prepare(
          "SELECT COUNT(*) AS n FROM approvals WHERE dotId=? AND status='pending'",
        )
        .get(input.dotId) as { n: number }
    ).n;
    if (pending >= MAX_PENDING_PER_DOT)
      throw new Error(
        'Too many approvals are already waiting for the owner. Stop and wait for decisions.',
      );
    const id = randomUUID();
    this.db
      .prepare(
        "INSERT INTO approvals(id, dotId, threadId, delegationId, kind, summary, details, status, createdAt) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
      )
      .run(
        id,
        input.dotId,
        input.threadId,
        input.delegationId,
        input.kind,
        input.summary,
        input.details,
        Date.now(),
      );
    return this.get(id)!;
  }
  decide(id: string, decision: 'approved' | 'declined'): Approval {
    const row = this.row(id);
    if (!row) throw new Error('Approval not found.');
    if (row.status !== 'pending')
      throw new Error('This approval was already decided.');
    this.db
      .prepare('UPDATE approvals SET status=?, decidedAt=? WHERE id=?')
      .run(decision, Date.now(), id);
    return this.get(id)!;
  }
  /**
   * Spend an approval on one gated step. Throws unless the owner approved it
   * for this Dot, recently, and it has uses left.
   */
  consume(id: string, dotId: string, now = Date.now()): Approval {
    const row = this.row(id);
    if (!row || row.dotId !== dotId)
      throw new Error('That approval_id does not belong to this Dot.');
    const status: ApprovalStatus = this.view(row, now).status;
    if (status === 'pending')
      throw new Error(
        'The owner has not decided this approval yet. Stop and wait for the decision.',
      );
    if (status === 'declined')
      throw new Error('The owner declined this action. Do not perform it.');
    if (status === 'expired')
      throw new Error('This approval expired. Request a new one.');
    if (row.uses >= APPROVAL_MAX_USES)
      throw new Error('This approval has been used up. Request a new one.');
    this.db
      .prepare(
        "UPDATE approvals SET status='used', usedAt=?, uses=uses+1 WHERE id=?",
      )
      .run(now, id);
    return this.get(id)!;
  }
}
