# renard-agent

A personal AI agent running in production on a small VPS. It does two things every day:

1. **Job watch** — collects job alerts from email, reads each full job posting with a real browser,
   pre-sorts them without AI, then has the agent triage them against written criteria and email a
   short list of applications worth preparing.
2. **Market alerts** — watches a few crypto assets and wakes the LLM up only when a price crosses
   a threshold (Hermes monitor-script pattern).

The agent engine is **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** by Nous Research
(MIT). This repository does not contain Hermes. My work is the assembly around it: deployment, the
scheduled pipelines, the collection and browser scripts, the agent instructions, and the security
and cost choices described below.

Code comments are in French.

## Architecture

```
                        VPS (Ubuntu, Docker, no published ports)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                                                                              │
 │  05:30  host cron (root) ── jobs/veille-emploi.sh                            │
 │           │                                                                  │
 │           ├─ 1. collect ──► [hermes container, uid 10000]                    │
 │           │       jobs/collecte_emploi.py                                    │
 │           │       IMAP (read-only) on the agent's mailbox                    │
 │           │       → parse LinkedIn / Indeed / HelloWork alert emails         │
 │           │       → dedupe against 90-day state, count announced vs parsed   │
 │           │                                                                  │
 │           ├─ 2. read ─────► [browser container "li-nav", uid 10001]          │
 │           │       browser/lire_annonces.py over CDP (127.0.0.1 only)         │
 │           │       plain Chromium + Xvfb, logged in by the human via noVNC    │
 │           │       → full text of each posting, human pace, stops at the      │
 │           │         first verification page                                  │
 │           │                                                                  │
 │           └─ 3. write ────► data/emploi/jour/YYYY-MM-DD.json                 │
 │                                                                              │
 │  08:00  Hermes scheduled task (LLM)                                          │
 │           jobs/preparer_analyse.py  (pre-run script, its stdout feeds the    │
 │           prompt: git pull of the profile repo + today's postings)           │
 │           → agent triages with examples/agent_instructions.example.md        │
 │           → jobs/envoyer_mail.py  (fixed recipient) → Telegram: 2-line notice│
 │                                                                              │
 │  every 15 min  Hermes monitor task  market/alertes_marche.py                 │
 │           → LLM called only if the script's output changed                   │
 └──────────────────────────────────────────────────────────────────────────────┘
                                   │
                         email + Telegram notice
                                   ▼
                     owner reads, decides and applies
```

## Files

| Path | Role |
|---|---|
| `jobs/collecte_emploi.py` | Email collector: IMAP read-only, one parser per job board, dedupe state, watchdog per source, title pre-filter (no AI). |
| `jobs/veille-emploi.sh` | Morning pipeline run by host cron: collect → read in browser → write the day file; degraded mode if the browser fails. |
| `jobs/preparer_analyse.py` | Pre-run script of the agent's task: refreshes the profile repo and prints the postings within a size budget; `--offre N` prints one posting in full. |
| `jobs/envoyer_mail.py` | Sends the agent's report. The recipient comes from a secret file, never from an argument. |
| `jobs/relire_source.py` | Re-reads the postings of one source that were blocked or unreadable, and merges them back. |
| `browser/` | Dockerfile and start script of the browser container, and the reader that connects to it. |
| `market/point_marche.py` | Computes market signals from CoinGecko (no API key). The LLM only comments numbers it is given. |
| `market/alertes_marche.py` | Monitor script: prints only threshold levels, with hysteresis, so the output changes only when it matters. |
| `deploy/compose.yaml` | Hermes container: image pinned by digest, memory cap, `no-new-privileges`, no ports. |
| `deploy/cron.example` | Host cron line and the Hermes task definitions. |
| `examples/` | Example agent instructions and secret file layout. |

## Cost choices

- **No AI where a script is enough.** Collection, parsing, deduplication and the title pre-filter
  are plain Python. The LLM is called once a day, on postings that are already fetched and trimmed.
- **Size budget.** `preparer_analyse.py` gives each posting a share of a fixed character budget;
  the agent opens a full posting only when it needs to (`--offre N`).
- **Monitor scripts.** Market alerts run every 15 minutes but the LLM is invoked only when the
  script output changes. The output contains threshold levels, not prices, so it is stable most of
  the time. Hysteresis prevents flapping around a threshold.
- **Model per task.** Each scheduled task sets its own model and reasoning effort.
- The whole setup runs on a subscription plan plus a small VPS.

## Security and governance

- **Secrets outside the code.** Credentials live in files on the server (`600`, owned by the agent's
  user), written by the human. Scripts read them; they are never passed on the command line, never
  printed, never in this repository. See `examples/mail-secrets.example`.
- **Least privilege.**
  - Containers run as non-root users with `no-new-privileges`, a memory cap, and no published
    ports. The browser's debugging port listens on `127.0.0.1` inside its container only.
  - The agent has a **read-only** deploy key for the profile repository, and **no access to the
    logged-in browser**: the browser is driven by the host's cron, not by the agent.
  - The mail script has a single, fixed recipient.
- **Prompt-injection defence.** Job postings and emails are untrusted text. The agent that reads them
  cannot act on the job sites or the browser session. Its instructions state that posting text is
  data, never instructions, and that any attempt to give it orders must be reported.
- **No anti-bot circumvention.** The browser is a stock Chromium: no stealth plugins, no proxies, no
  CAPTCHA solvers. The human logs in through noVNC. The reader stops at the first verification
  page, and the affected posting is listed at the top of the report with its direct link so the
  owner can open it and pass the check in person.
- **Human in the loop.** The agent never applies and never contacts anyone. It sends a short list
  of applications worth preparing; the human decides and sends.
- **Fail loudly.** Every stage reports what it could not do: emails it could not parse, alerts with
  fewer postings than announced, sources silent for too long, postings read in full / as an excerpt /
  by title only. The agent is told never to stay silent.

## Running it

This is not a packaged product: paths (`/srv/hermes`, `/opt/data`) match my server. To reproduce:

1. Deploy Hermes with `deploy/compose.yaml` and connect it to a messaging channel.
2. Build the browser image from `browser/`, run it with the profile volume, open noVNC through an
   SSH tunnel and log in to the job sites yourself.
3. Copy `jobs/*.py` to the agent's script folder, create the secret file from
   `examples/mail-secrets.example`, and install `jobs/veille-emploi.sh` and the cron line from
   `deploy/cron.example`.
4. Write your own instructions from `examples/agent_instructions.example.md`.

## License

MIT, see `LICENSE`. Hermes Agent is a separate project under its own license.
