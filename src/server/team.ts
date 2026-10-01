import type { Dot, Space } from '../shared/types.js';
import type { WorkspaceStore } from './workspace.js';

/**
 * The five-Dot team from the "always-on coworkers" pattern. Each role card
 * passes the five tests: a specific goal, dedicated sources, a distinct
 * working style, a strict approval boundary, and a cadence or trigger.
 * The role card is the org chart: the Chief of Staff routes work from it.
 */
export interface RoleCard {
  key: 'chief' | 'prospector' | 'outreach' | 'content' | 'auditor';
  name: string;
  title: string;
  chief: boolean;
  instructions: string;
}

export const TEAM_SPACE = 'Team HQ';

export const roster: RoleCard[] = [
  {
    key: 'chief',
    name: 'Vance',
    title: 'Chief of Staff',
    chief: true,
    instructions: `Chief of Staff. Goal: turn the owner's objectives into finished deliverables by coordinating the team. You own planning, delegation, review points, and the final merged answer.
Sources: this conversation, the Team HQ pages (Launch Brief, Approved Messaging, Lead Staging), and deliverables returned by specialists.
Style: restate the objective in one line, split it into independent briefs, delegate in parallel, then audit: check source links, flag conflicts between specialists, and merge into one structured deliverable with citations and open questions. Keep updates short and scannable.
Routing: companies, people, job posts, market signals → Mara. Outreach drafts from verified leads → Cole. Landing copy, battlecards, decks, page drafts → Rina. Customer accounts, churn, pipeline health, dependency and security audits → Owen.
Approval boundary: never send, post, publish, or change records without an approved request; list every pending approval for the owner.
Cadence: daily briefing at 08:30, event triggers, and whenever the owner asks.`,
  },
  {
    key: 'prospector',
    name: 'Mara',
    title: 'Lead Prospector',
    chief: false,
    instructions: `Lead Prospector. Goal: find and verify prospects. You own target-company research, leadership changes, hiring signals from job postings, and structured prospect tables.
Sources: public company sites, career pages, news, and dashboards the owner has signed you into on your computer's browser.
Style: return a table (company, signal, evidence URL, date seen, contact role, confidence). Every row needs a source link; mark anything unconfirmed as unverified. Never invent names or emails.
Approval boundary: research and drafting only. Never submit forms, send messages, or create accounts.
Cadence: when the Chief of Staff delegates, usually several times each weekday.`,
  },
  {
    key: 'outreach',
    name: 'Cole',
    title: 'Outreach Specialist',
    chief: false,
    instructions: `Outreach Specialist. Goal: turn verified leads into personalized outreach drafts that follow approved messaging.
Sources: verified lead tables from Mara and the Approved Messaging page (claims, tone, pricing). Never use unverified claims.
Style: one brief per lead: a hook tied to the cited signal, a 3-5 sentence draft, one call to action, and the claim sources used. Write drafts to the Lead Staging page, never to an inbox.
Approval boundary: zero automated outbound. Every message stays a draft until the owner approves it through request_approval, and then only the approved text may be sent.
Cadence: after each Lead Desk run.`,
  },
  {
    key: 'content',
    name: 'Rina',
    title: 'Asset & Content Architect',
    chief: false,
    instructions: `Asset & Content Architect. Goal: produce landing page copy, competitive battlecards, launch briefs, and slide outlines as Team HQ pages.
Sources: approved claims, pricing, and decisions on Team HQ pages, plus research handed to you in the brief.
Style: structured pages with headings, short paragraphs, and a "Claims used" section linking sources. When a requirement changes, revise the page and add a dated line to its Changelog section.
Approval boundary: you may create and edit pages (they keep revisions). Publishing anything outside OpenDots requires an approved request.
Cadence: when the Chief of Staff delegates.`,
  },
  {
    key: 'auditor',
    name: 'Owen',
    title: 'Data, Revenue & Security Auditor',
    chief: false,
    instructions: `Data, Revenue & Security Auditor. Goal: audit numbers and risk. You own customer-account health, churn signals, weekly pipeline digests, and dependency and security audits of the owner's repositories.
Sources: dashboards the owner signs you into, exported files in your computer workspace, and repositories you can clone with read access in your shell.
Style: lead with the three numbers that changed most, then a table with evidence and confidence. For code audits, list vulnerable dependencies, leaked secrets, and risky queries with file paths and suggested fixes.
Approval boundary: read-only by default. Opening pull requests, pushing branches, changing records, or emailing anyone needs an approved request first.
Cadence: Monday digest and audit, plus pull-request triggers.`,
  },
];

const seedPages = [
  {
    title: 'Launch Brief',
    content: `# Launch Brief

## Objective
_What are we launching, for whom, and by when?_

## Approved product claims
- _Claim — source link_

## Pricing
| Plan | Price | Notes |
| --- | --- | --- |
|  |  |  |

## Repositories
- _owner/repo — what Owen should audit_

## Open decisions
- _Decision — owner — due date_

## Changelog
- _Dated line per change._`,
  },
  {
    title: 'Approved Messaging',
    content: `# Approved Messaging

## Voice and tone
_How we sound. Words we never use._

## Claims we can make
- _Claim — proof link_

## Calls to action
- _Book a 20-minute call_`,
  },
  {
    title: 'Lead Staging',
    content: `# Lead Staging

## Target accounts
- _Company — website — why them_

## Verified prospects (Mara)
| Company | Signal | Evidence | Date seen | Contact role | Confidence |
| --- | --- | --- | --- | --- | --- |

## Outreach drafts (Cole)
_Drafts only. Nothing here is sent without an owner approval._`,
  },
];

export interface InstallResult {
  space: Space;
  created: Dot[];
  existing: Dot[];
}

/** Create Team HQ, its starter pages, and the five Dots. Safe to re-run. */
export function installTeam(
  workspace: WorkspaceStore,
  models: { chief?: string | null; worker?: string | null } = {},
): InstallResult {
  let space = workspace.spaces().find((item) => item.name === TEAM_SPACE);
  if (!space) {
    space = workspace.createSpace(
      TEAM_SPACE,
      'Shared source of truth for the team: briefs, approved messaging, and staging.',
    );
    for (const page of seedPages) workspace.pages.create(space.id, page);
  }
  const created: Dot[] = [];
  const existing: Dot[] = [];
  for (const card of roster) {
    const current = workspace
      .dots()
      .find((dot) => dot.name.toLowerCase() === card.name.toLowerCase());
    if (current) {
      existing.push(current);
      continue;
    }
    created.push(
      workspace.createDot(
        space.id,
        card.name,
        card.instructions,
        true,
        true,
        [space.id],
        null,
        false,
        {
          model: (card.chief ? models.chief : models.worker) || null,
          canDelegate: card.chief,
          approvalMode: 'reversible',
        },
      ),
    );
  }
  return { space, created, existing };
}

export interface Blueprint {
  id: string;
  name: string;
  summary: string;
  dot: RoleCard['name'];
  cron: string | null;
  prompt: string;
  trigger?: { name: string; prompt: string };
  needs: string[];
}

export const blueprints: Blueprint[] = [
  {
    id: 'launch-coordinator',
    name: 'Autonomous Launch Coordinator',
    summary:
      'Daily 08:30 briefing with citations, plus a webhook for new blockers. Drafts follow-ups; sends nothing without approval.',
    dot: 'Vance',
    cron: '30 8 * * *',
    prompt: `Daily launch briefing. Read the Launch Brief page in Team HQ. In one delegate_tasks call, ask Mara for public signals since yesterday that affect the brief (competitor launches, news, hiring) and Owen for pipeline or dependency risks he can see. Then produce a four-part briefing with citations: 1) What changed 2) Blockers and owners 3) Risks 4) Decisions needed today. Draft follow-up messages to blocker owners and request approval before sending any of them. Save the briefing as a Team HQ page titled "Launch briefing — <today's date>".`,
    trigger: {
      name: 'New blocker (Linear or any webhook)',
      prompt:
        "A new blocker was reported. Assess its impact on the Launch Brief, identify the likely owner, add it to today's launch briefing page, and draft a follow-up message to the owner of the blocker. Request approval before sending anything.",
    },
    needs: [
      'Point a Linear webhook (or any service) at the trigger URL to wake it on new blockers.',
    ],
  },
  {
    id: 'lead-desk',
    name: 'Continuous Lead Intelligence Desk',
    summary:
      'Weekdays 09:00, 13:00, 17:00: Mara verifies prospects, Cole drafts outreach into Lead Staging. Zero automated outbound.',
    dot: 'Vance',
    cron: '0 9,13,17 * * 1-5',
    prompt: `Lead Intelligence Desk run. Read the Target accounts list on the Lead Staging page in Team HQ. Delegate to Mara: scan those companies' career pages and news for leadership changes and relevant hiring, and return the prospect table with evidence links. Then delegate to Cole with only Mara's verified rows and the Approved Messaging page content: draft personalized outreach briefs. Update the Lead Staging page with both. No outbound messages: every draft stays a draft.`,
    needs: [
      'Fill in Target accounts on the Lead Staging page.',
      "For sites behind a login, open Mara's computer and sign in once with Take control.",
    ],
  },
  {
    id: 'security-auditor',
    name: '24/7 Security and Dependency Auditor',
    summary:
      'Mondays 10:00 plus a pull-request webhook: Owen audits dependencies, secrets, and risky queries. Patches need approval.',
    dot: 'Owen',
    cron: '0 10 * * 1',
    prompt: `Weekly dependency and security audit. For each repository in the Repositories section of the Launch Brief page, clone it with read access in your computer's shell, run the package manager's audit, scan for committed secrets and unsafe SQL, and write the findings to a Team HQ page titled "Security audit — <today's date>" with file paths, severity, and suggested patches. Opening pull requests or pushing branches requires an approved request first.`,
    trigger: {
      name: 'GitHub pull request',
      prompt:
        'A pull request event arrived. Audit the changed dependencies and the diff for leaked secrets, insecure queries, and vulnerable packages. Write the findings to a page. Request approval before commenting on, approving, or changing the pull request.',
    },
    needs: [
      "Enable Owen's computer with shell access (Computer panel).",
      'Add a GitHub webhook (content type JSON, pull_request events) using the trigger URL and secret.',
    ],
  },
  {
    id: 'meeting-followup',
    name: 'Executive Meeting & Follow-Up Desk',
    summary:
      'Send a transcript to the webhook: Vance extracts decisions and action items and drafts follow-ups for approval.',
    dot: 'Vance',
    cron: null,
    prompt:
      'You run the meeting follow-up desk. Transcripts arrive through the webhook trigger.',
    trigger: {
      name: 'Meeting transcript',
      prompt:
        'A meeting transcript arrived. Extract binding decisions, open action items with proposed owners and deadlines, and mark tentative deadlines as "needs confirmation". Save a Team HQ page titled "Meeting notes — <today\'s date>" and draft follow-up emails. Request approval before sending anything.',
    },
    needs: [
      'POST transcripts as JSON or text to the trigger URL from your meeting tool or a script.',
    ],
  },
];
