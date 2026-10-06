# Dots Fam

**An always-on AI team you run yourself.** You talk to a Chief of Staff. It splits the work and hands it to specialists. Each one has its own model, its own browser and its own role. They keep working on schedules and webhooks while you are away, and they stop to ask you before anything that can't be undone.

Built from scratch on **LangGraph + FastAPI + React**. It works through chat, Slack and voice calls.

![Vance, the Chief of Staff, hands a lead hunt to Mara and Cole and merges their results](docs/screenshots/chat.png)

## What you get

| | |
| --- | --- |
| **A team, not a chatbot** | Vance (Chief of Staff) delegates to Mara (leads), Cole (outreach), Rina (content) and Owen (data and security). Role cards are the org chart: edit them, add Dots, give each its own model. |
| **Parallel, isolated handoffs** | Each specialist gets one self-contained brief in its own thread, runs in parallel, and reports back. Late results arrive as a team update in your conversation. |
| **The Reversibility Law** | Dots read, research and draft freely. Sending, posting, paying, deleting, pushing or submitting pauses for your approval: in the web app, with Slack buttons, or by saying "yes" on a call. Declines go back to the Dot so it can change course. |
| **Web research** | Every Dot can search the web, read and crawl pages, and get quick cited answers through [Exa](https://exa.ai). When a question needs current facts, the Dot searches, reads the best sources and answers with links. A per-task budget stops runaway searching. |
| **Routines and webhooks** | Cron schedules in your time zone ("weekdays at 9, 1 and 5") and signed webhooks (GitHub and Linear signatures verified, rate-limited, payload treated as untrusted). |
| **A computer per Dot** | A browser that remembers its logins, a workspace for files, and an optional shell inside the Dot's own Docker container. Watch the live screen, take control, hand it back. |
| **Pages** | Shared Markdown documents with revisions and conflict checks. Dots write briefs and staging tables here; you edit them in a rich editor. |
| **Slack** | Mention the app or DM it. Every Slack thread is one conversation, and `Mara: ...` talks to Mara directly. Routine results and approvals can go to a channel. |
| **Your team calls you** | When a Dot needs your OK, your phone rings: "Vance is calling". Answer, hear what's waiting, say yes or no. Free web push or the ntfy app, so no phone number, SIM or Twilio bill. |
| **Voice calls** | Call any Dot from the browser. Open models by default: Whisper to hear you, Kokoro to talk back, Smart Turn to know when you're done talking. |
| **Kill switches** | Pause the whole team, stop one run, stop one handoff, or switch a Dot between "ask before acting" and autonomous. |
| **Blueprints** | One click installs a working setup: Autonomous Launch Coordinator, Continuous Lead Intelligence Desk, 24/7 Security and Dependency Auditor, or Executive Meeting & Follow-Up Desk. Each comes with its routine and/or webhook. |

| Approvals | Handoffs | Computer |
| --- | --- | --- |
| ![](docs/screenshots/approvals.png) | ![](docs/screenshots/handoffs.png) | ![](docs/screenshots/computer.png) |

| Voice call | Routines and webhooks | Pages |
| --- | --- | --- |
| ![](docs/screenshots/call.png) | ![](docs/screenshots/routines.png) | ![](docs/screenshots/pages.png) |

## Quick start

You need Python 3.11+, Node 20+ and a key for at least one model provider (Anthropic, OpenAI or OpenRouter). Add an `EXA_API_KEY` (exa.ai) too if you want the Dots to search the web.

```bash
git clone https://github.com/tanuku-saikarthik/dots-fam.git && cd dots-fam
cp .env.example .env            # add your API keys; set DEFAULT_TIMEZONE

python -m venv .venv && . .venv/bin/activate
pip install -e backend          # or: uv venv && uv pip install -e backend

(cd frontend && npm install && npm run build)

dotsfam                         # → http://127.0.0.1:8787
```

The first start creates Team HQ: five Dots and three seed pages. Vance uses `DEFAULT_MODEL`, the specialists use `WORKER_MODEL`. If you only have one provider, point both at it, for example `anthropic:claude-sonnet-4-5` and `anthropic:claude-haiku-4-5`. Any Dot can switch models under **Team and setup**. Models are written as `provider:model`, and `openrouter:...` reaches hundreds more.

**On Windows**, the smoothest route is WSL 2: run `wsl --install -d Ubuntu`, open Ubuntu, follow the steps above, and open http://localhost:8787 in your Windows browser. For voice calls, set `networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig`. Windows' Smart App Control doesn't check Linux programs, so nothing gets blocked.

To run it natively in PowerShell instead:

```powershell
git clone https://github.com/tanuku-saikarthik/dots-fam.git; cd dots-fam
Copy-Item .env.example .env; notepad .env      # add your keys
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1                  # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -e backend
cd frontend; npm install; npm run build; cd ..
python -m dotsfam.main                          # same as `dotsfam`
```

Use `python -m ...` for every command (`python -m dotsfam.main`, `python -m playwright install chromium`). pip creates a small `.exe` launcher for each command, and each one is unique to your install. With Smart App Control on, Windows blocks those launchers because it has never seen them before. The signed `python.exe` is allowed. If Smart App Control still blocks a library file, use WSL 2, or turn it off while you install. Since the April 2026 update (KB5083769) you can turn it back on afterwards: Windows Security, App & browser control, Smart App Control settings.

Computers need Docker Desktop (with WSL 2) for `COMPUTER_DRIVER=docker`. Voice works natively too: Whisper uses your CPU or an NVIDIA GPU, and Kokoro and Smart Turn run on ONNX Runtime.

**Check web search.** With `EXA_API_KEY` in `.env`, run `python -m dotsfam.check web "who is hiring AI engineers in Bengaluru?"`. It does one search, one page read and one cited answer the way a Dot would, and prints the cost of each.

**Running it for real.** Bind to a public address only with `OWNER_TOKEN` set (the server refuses otherwise), put it behind HTTPS, and set `PUBLIC_URL` so webhook URLs are correct. State lives in `data/` (two SQLite files), so back that folder up.

**Developing.** Run `dotsfam` and `cd frontend && npm run dev`, then open http://localhost:5174. Vite proxies `/api` and `/hooks` to the backend.

## Turning on the extras

Each one is optional and off until you configure it.

### A computer for each Dot

```bash
pip install -e "backend[computer]"
docker build -f computer/Dockerfile -t dotsfam-computer:latest .
# .env: COMPUTER_DRIVER=docker
```

Then open **Computers** and switch one on for a Dot. Each Dot gets its own container (`dotsfam-computer-<id>`) and its own volume for files and browser profile. The container is published on `127.0.0.1` only and needs a per-Dot token. The shell is off until you turn it on, and commands that push, deploy, post or call `curl -X POST` still ask you first.

`COMPUTER_DRIVER=local` runs one headless Chromium per Dot on the host instead (`playwright install chromium` first). Use it for development only: there is no isolation, so the shell is always off.

### Slack

1. Create an app from [`docs/slack-app-manifest.yaml`](docs/slack-app-manifest.yaml) (api.slack.com/apps, then **From a manifest**).
2. Generate an app-level token with `connections:write` and install the app to your workspace.
3. Set `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` and `SLACK_ALLOWED_USERS` (your member ID). Optionally set `SLACK_NOTIFY_CHANNEL` for routine results and approvals.
4. Run `pip install -e "backend[slack]"` and restart.

Socket Mode means no public URL is needed. Only allowed members can talk to the team or press Approve.

### Voice calls

```bash
pip install -e "backend[voice]"     # Pipecat with WebRTC, Silero, Whisper and Kokoro
```

Press **Call** in any conversation. Each thing you say shows up in the chat, and the Dot answers out loud. The first call downloads the models: Whisper Large V3 Turbo (about 1.6 GB) and Kokoro (about 350 MB). On a modest CPU, set `VOICE_WHISPER_MODEL=small` for faster replies.

- Browsers only allow the microphone on `https://` or `localhost`.
- To call across networks, set `VOICE_ICE_SERVERS`. A TURN server helps behind strict NATs.
- `VOICE_STACK=openai` swaps in OpenAI speech-to-text and text-to-speech. The Dot, its tools and its approvals stay the same.
- `VOICE_STACK=off` turns calls off.

| Piece | Default | License |
| --- | --- | --- |
| Call framework | [Pipecat](https://github.com/pipecat-ai/pipecat), peer-to-peer WebRTC | BSD-2 |
| Hearing | Whisper Large V3 Turbo via faster-whisper | MIT |
| Speaking | Kokoro-82M via kokoro-onnx | Apache-2.0 |
| Turn-taking | Silero VAD + Smart Turn v3 | MIT / BSD-2 |

### Your team calls you

<img src="docs/screenshots/incoming-call.png" width="260" align="right" alt="Vance is calling, with the two emails waiting for approval">

When a run stops for your approval, Dots Fam rings your phone. Tap **Answer** and the voice call starts in the conversation, where the Dot reads out what's waiting and you approve by saying yes. Several approvals in one conversation ring once, at most once a minute.

Both ways of ringing are free and need no phone number:

- **Web push.** Open Team and setup, press **Turn on calls**, and allow notifications. Works in Chrome, Edge, Firefox and Safari, even with the tab closed. Needs `https://` (or `localhost`). On iPhone, add Dots Fam to your Home Screen first (iOS 16.4 or later) and turn calls on from there.
- **ntfy.** Install the [ntfy](https://ntfy.sh) app, subscribe to a long, hard-to-guess topic, and set `NTFY_TOPIC`. Alerts come in at urgent priority with an **Answer** button. Anyone who knows a topic name on ntfy.sh can read it, so there the alert only says which Dot is calling; you hear the details when you answer. With your own ntfy server (`NTFY_SERVER`) or an access token (`NTFY_TOKEN`), the alert includes them. Web push is end-to-end encrypted, so it always does.

Set `PUBLIC_URL` so the ntfy **Answer** button opens your server. **Test call** in Team and setup rings every device you set up.

<br clear="right">

### Build mode: leave, come back to a pull request

Give a Dot your project folder (Team and setup, Claude Code capabilities) and tick **Build mode**. It then codes on its own git branch, in a separate copy of the project under your data folder:

- **Free, no approvals:** reading, writing and editing files, installs, builds, tests and local commits.
- **Asks you (rings your phone):** pushing the branch and opening the pull request, and writing secret-looking files.
- **Your checkout is never touched.** Your branch, uncommitted work and `.env` stay where they are. Commits made by the Dot are left out if they include secret-looking files.
- **Commands run in a throwaway Docker container** that sees only that branch: no capabilities, memory/CPU/process limits, a time limit. Build the image once with `docker build -t dotsfam-build sandbox/`, or set `BUILD_IMAGE`. Without Docker, commands run on the server and ask first.
- Specialists share the branch of the conversation they were delegated from. Pull requests use the GitHub CLI (`gh`) when it is installed, otherwise you get the compare link.

The project must be a git repository with at least one commit and a remote you can push to from the server.

## Run it 24/7 for free (Oracle Cloud)

Dots Fam only works while its server is on. A free [Oracle Cloud Always Free](https://www.oracle.com/cloud/free/) ARM VM (Ampere A1, up to 4 cores and 24 GB at the time of writing; check their current terms) is enough for the whole team.

1. Create an **Ubuntu 24.04** VM on the A1 shape and SSH in.
2. Run:

```bash
git clone https://github.com/tanuku-saikarthik/dots-fam && cd dots-fam
./deploy/install.sh
```

3. Add `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` to `.env`, then `sudo systemctl restart dotsfam`.

The script installs Docker, Python 3.12 (via uv) and Node, builds the web app and the two Docker images (Dot computers and the build sandbox), writes `.env` with a random `OWNER_TOKEN`, runs Dots Fam as a systemd service that restarts on failure and reboot, and sets up HTTPS. Re-running it updates; `./deploy/update.sh` pulls first.

| `MODE=` | HTTPS | Opens ports | Best for |
| --- | --- | --- | --- |
| `tailscale` (default) | Private `*.ts.net` address, only your own devices | None | Just you. Install the Tailscale app on your phone |
| `cloudflare` | Your hostname via a free Cloudflare Tunnel (`CF_TUNNEL_TOKEN`, `PUBLIC_URL`) | None | Public webhooks without opening ports |
| `caddy` | Automatic certificate for your `DOMAIN` | 80 and 443 | You own a domain |
| `none` | You provide it | None | Your own proxy |

Notes:
- The app listens on `127.0.0.1` only; the HTTPS front door is the only way in.
- It runs on the host, not in a container, because it starts Docker containers for each Dot's computer and for build mode. Mounting the Docker socket into a container would give it root on the machine anyway.
- `VOICE=1 ./deploy/install.sh` adds local voice (Whisper and Kokoro, wants 4+ GB RAM). Without it, voice is off; you can set `VOICE_STACK=openai` instead.
- Phone calls and the microphone need HTTPS, which every mode above gives you except `none`.
- Written for Ubuntu and Debian. The script's file and option handling is tested; the full install has not been run on a real Oracle VM yet.

## How it works

```
 Web app ─┐                        ┌─ one LangGraph graph per run:
 Slack  ──┼─ FastAPI ─ RunManager ─┤    agent → gate → tools → agent
 Voice  ──┘     │                  │    (the gate pauses with interrupt() for approvals)
                │                  └─ DelegationManager: parallel, isolated briefs
                ├─ Scheduler: cron routines, webhook fires, team follow-ups
                ├─ ComputerManager: one container per Dot (Playwright service inside)
                └─ SQLite: team, pages, tasks, approvals, events  +  LangGraph checkpoints
```

- **A Dot** is a row with a role card, a model, permissions and a color. Every run builds a small LangGraph state graph with that Dot's tools, and the conversation lives in a checkpointed thread.
- **The gate** looks at each tool call before it runs. Tools are marked as external (always ask) or carry a classifier: browser clicks are judged by the name of the button, shell commands by pattern. Anything that needs you is saved as an approval and the graph pauses with `interrupt()`. Your decision resumes exactly that step with `Command(resume=...)`, so nothing re-runs and nothing is skipped.
- **Delegation** creates an internal thread per specialist with only the brief. If a specialist needs approval, its card shows up in your conversation, in Slack or on the call. When the work lands, the Chief gets a follow-up turn to merge it.
- **Runs stream** to the browser over server-sent events: text as it is written, tool steps, handoffs and approvals.

## Security notes

- One owner per server. `OWNER_TOKEN` protects the API and UI; keep the server on localhost or behind HTTPS.
- When a step can't be checked (an element missing from the latest snapshot, a keyboard shortcut, Enter outside a search box), the Dot asks first. Before acting, the computer confirms that the element still matches the snapshot and that focus is where the classifier assumed.
- The browser classifier is a guardrail, not a sandbox. It catches the usual words ("Send", "Pay", "Delete", "Publish", "Submit" and similar). A site that labels its purchase button "Continue" will get past it. Keep important Dots on **Ask before acting**, give computers only the logins they need, and leave the shell off unless a Dot needs it.
- Web pages, files, Slack messages and webhook payloads are handed to Dots as untrusted data. Prompt injection is still a real risk with any agent, and the approval gate is your backstop.
- `read_web_page` resolves a host once, refuses private addresses, and connects to the IP it checked, so DNS rebinding can't point it at your network.
- Webhook secrets are per trigger and can be rotated. Each trigger is limited to 30 fires an hour.

## Honest limits

- Single owner. There are no multi-user accounts or roles yet.
- The Docker computer image and real Whisper downloads are not exercised in CI. Tests use the local driver with real Chromium, a real WebRTC call and stubbed Whisper weights. Kokoro speech was checked end to end by hand.
- Every routine run spends model tokens. Watch your schedules.
- Voice works best in English. Whisper and Kokoro support other languages through `VOICE_LANGUAGE`, but the turn detector is tuned for English.

## Development

```bash
pip install -e "backend[dev,computer,slack,voice]"
cd backend && pytest && ruff check . && ruff format --check .
cd ../frontend && npm run typecheck && npm run build
```

```
backend/dotsfam/   agent.py (graph + gate), runs.py, delegation.py, scheduler.py, webhooks.py
                   computers.py + computer_service.py + reversibility.py, slack.py, voice.py
                   api.py, db.py, team.py (roster, blueprints), tools/ (pages, web, team)
frontend/src/      views/ (Chat, Activity, Routines, Pages, Computers, Team), components/
computer/          Dockerfile for the per-Dot computer image
```

## Credits

Inspired by [monokern's post](https://x.com/monokern/status/2105277060478382207) on running an always-on agent team. Built with LangGraph, FastAPI, React, TipTap, Playwright and Pipecat, plus the open Whisper, Kokoro, Silero and Smart Turn models.

An earlier version built on CopilotKit's OpenDots lives on the [`opendots-version`](https://github.com/tanuku-saikarthik/dots-fam/tree/opendots-version) branch.

MIT License.
