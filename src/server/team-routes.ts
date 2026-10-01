import { Hono } from 'hono';
import { z } from 'zod';
import type { Approval, TeamState } from '../shared/types.js';
import { modelRefSchema, suggestedModels, workerModelRef } from './models.js';
import type { Platform } from './platform.js';
import { timezoneSchema } from './schedule.js';
import { blueprints, installTeam, roster } from './team.js';

const decisionPrompt = (
  approval: Approval,
  requester: string,
  forOtherDot: boolean,
) => {
  const approved = approval.status === 'approved';
  const what = `${approval.summary}${approval.details ? `\nApproved details:\n${approval.details}` : ''}`;
  if (forOtherDot)
    return approved
      ? `[Approval] The owner APPROVED ${requester}'s request (approval_id ${approval.id}): ${what}\nIf the action is still needed, delegate it back to ${requester} with a brief that includes approval_id ${approval.id} and exactly these details. Nothing beyond them.`
      : `[Approval] The owner DECLINED ${requester}'s request (approval_id ${approval.id}): ${approval.summary}\nDo not perform it. Adjust the plan and tell the owner what changes.`;
  return approved
    ? `[Approval] The owner APPROVED your request (approval_id ${approval.id}): ${what}\nCarry out exactly the approved action now, passing approval_id "${approval.id}" to each gated tool call. Do nothing beyond it, then report the outcome with evidence.`
    : `[Approval] The owner DECLINED your request (approval_id ${approval.id}): ${approval.summary}\nDo not perform it. Acknowledge briefly and suggest an alternative if useful.`;
};

export function teamRoutes(platform: Platform) {
  const app = new Hono();
  const { workspace } = platform;
  const hookUrl = (id: string, origin: string) =>
    `${(platform.config.publicUrl ?? origin).replace(/\/$/, '')}/hooks/${id}`;

  app.get('/team', (c) => {
    const delegations = workspace.delegations.list(60);
    const state: TeamState = {
      delegations,
      events: workspace.delegations.events(delegations.map((item) => item.id)),
      approvals: workspace.approvals.list(100),
      triggers: workspace.triggers.list(),
    };
    return c.json({
      ...state,
      roster: roster.map(({ name, title, chief }) => ({ name, title, chief })),
      blueprints,
      models: suggestedModels,
      pendingApprovals: workspace.approvals.pendingCount(),
      hookBase: hookUrl('', new URL(c.req.url).origin).replace(/\/$/, ''),
    });
  });

  app.post('/approvals/:id/decision', async (c) => {
    const parsed = z
      .object({ decision: z.enum(['approved', 'declined']) })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success) return c.json({ error: 'Approve or decline.' }, 400);
    let approval: Approval;
    try {
      approval = workspace.approvals.decide(
        c.req.param('id'),
        parsed.data.decision,
      );
    } catch (error) {
      return c.json(
        { error: error instanceof Error ? error.message : 'Not found.' },
        409,
      );
    }
    let delivered = false;
    if (approval.threadId) {
      try {
        const thread = workspace.requireThread(approval.threadId);
        const requester = workspace.dot(approval.dotId)?.name ?? 'The Dot';
        platform.team.followUp(
          approval.threadId,
          decisionPrompt(approval, requester, thread.dotId !== approval.dotId),
        );
        delivered = true;
      } catch {
        // The conversation was removed; the decision is still recorded.
      }
    }
    return c.json({ approval, delivered });
  });

  app.post('/delegations/:id/cancel', (c) =>
    platform.team.delegations.cancel(c.req.param('id'))
      ? c.json({ ok: true })
      : c.json({ error: 'That work is not running.' }, 409),
  );

  app.post('/triggers', async (c) => {
    const parsed = z
      .object({
        name: z.string().trim().min(1).max(80),
        threadId: z.string().min(1),
        prompt: z.string().trim().min(3).max(4000),
      })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success)
      return c.json(
        { error: 'A trigger needs a name, a conversation, and instructions.' },
        400,
      );
    try {
      workspace.requireThread(parsed.data.threadId);
    } catch {
      return c.json({ error: 'Choose one of your conversations.' }, 403);
    }
    const { trigger, secret } = workspace.triggers.create(
      parsed.data.name,
      parsed.data.threadId,
      parsed.data.prompt,
    );
    return c.json(
      { trigger, secret, url: hookUrl(trigger.id, new URL(c.req.url).origin) },
      201,
    );
  });
  app.post('/triggers/:id/rotate', (c) => {
    try {
      return c.json({ secret: workspace.triggers.rotate(c.req.param('id')) });
    } catch {
      return c.json({ error: 'Trigger not found.' }, 404);
    }
  });
  app.patch('/triggers/:id', async (c) => {
    const parsed = z
      .object({ enabled: z.boolean() })
      .strict()
      .safeParse(await c.req.json().catch(() => null));
    if (!parsed.success) return c.json({ error: 'Invalid trigger.' }, 400);
    try {
      return c.json(
        workspace.triggers.setEnabled(c.req.param('id'), parsed.data.enabled),
      );
    } catch {
      return c.json({ error: 'Trigger not found.' }, 404);
    }
  });
  app.delete('/triggers/:id', (c) =>
    workspace.triggers.remove(c.req.param('id'))
      ? c.json({ ok: true })
      : c.json({ error: 'Trigger not found.' }, 404),
  );

  app.post('/team/install', async (c) => {
    const parsed = z
      .object({
        chiefModel: modelRefSchema.nullable().optional(),
        workerModel: modelRefSchema.nullable().optional(),
      })
      .strict()
      .safeParse(await c.req.json().catch(() => ({})));
    if (!parsed.success)
      return c.json(
        { error: 'Models must be written as provider:model.' },
        400,
      );
    const result = installTeam(workspace, {
      chief: parsed.data.chiefModel,
      worker: parsed.data.workerModel ?? workerModelRef(platform.config),
    });
    return c.json(result, 201);
  });

  app.post('/blueprints/:id/install', async (c) => {
    const blueprint = blueprints.find((item) => item.id === c.req.param('id'));
    if (!blueprint) return c.json({ error: 'Blueprint not found.' }, 404);
    const parsed = z
      .object({ timezone: timezoneSchema.optional() })
      .strict()
      .safeParse(await c.req.json().catch(() => ({})));
    if (!parsed.success)
      return c.json({ error: 'Use an IANA time zone.' }, 400);
    const dot = workspace
      .dots()
      .find((item) => item.name.toLowerCase() === blueprint.dot.toLowerCase());
    if (!dot)
      return c.json(
        { error: `Install the team first: ${blueprint.dot} is missing.` },
        409,
      );
    if (platform.setup().missing.length)
      return c.json(
        { error: `Setup required: ${platform.setup().missing.join(', ')}.` },
        503,
      );
    let conversation;
    try {
      conversation = await platform.createConversation(
        dot.id,
        `Routine · ${blueprint.name}`,
      );
    } catch (error) {
      return c.json(
        {
          error:
            error instanceof Error
              ? error.message
              : 'Could not create the routine conversation.',
        },
        503,
      );
    }
    const timezone = parsed.data.timezone ?? platform.config.timezone ?? 'UTC';
    let task;
    if (blueprint.cron) {
      task = platform.store.createTask(blueprint.prompt, null, {
        cron: blueprint.cron,
        timezone,
      });
      workspace.bindTask(task.id, conversation.id);
    }
    let trigger;
    if (blueprint.trigger) {
      const created = workspace.triggers.create(
        blueprint.trigger.name,
        conversation.id,
        blueprint.trigger.prompt,
      );
      trigger = {
        ...created,
        url: hookUrl(created.trigger.id, new URL(c.req.url).origin),
      };
    }
    return c.json({ conversation, task, trigger, needs: blueprint.needs }, 201);
  });
  return app;
}
