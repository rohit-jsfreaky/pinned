# CLAUDE.md — Pinned (HydraDB Hackathon, Track 1) — read this first, every session

**Pinned answers one question for coding agents: "is this code valid for the library version I actually have?"**
If not, it walks a HydraDB graph to the right call for THAT version and shows the chain
(`DataFrame.append` -REMOVED_IN-> pandas 2.0 -REPLACED_BY-> `pandas.concat`). It learns from its own answers: every
time an agent's code fails or passes, the graph changes, and the next answer is better. We prove it with an accuracy
curve on GitChameleon 2.0 (328 version-pinned coding problems with hidden tests).

## Deadline and what we submit
- **HARD cutoff: 9:00 AM IST, Mon 16 Nov 2026.** Aim to be done by 7 AM.
- Submit: (1) live hosted app, (2) open-source GitHub repo, (3) a Loom by Rohit (his screen, his voice).
- Track 1 bar (brief): "show an accuracy curve rising over iterations". FAQ: right metrics, a clear baseline, real
  continual-learning improvement (not one-off), the graph doing real work (would it fall apart on a plain store?).
- Judging = half engineering (real traversal, needs the graph, holds up under load), half go-to-market (posts on X /
  LinkedIn tagging HydraDB; who pays).
- Brief + facts: `docs/facts/brief.md`. HydraDB facts: `docs/facts/hydradb.md`. Dataset facts: `docs/facts/gitchameleon.md`.

## Rohit's working rules (15 Nov, override anything below)
1. Never talk to Rohit about time. Never cut scope because of time.
2. Website: ASK Rohit how he wants the design BEFORE any web design work. Core product (engine, API, MCP) we build ourselves.
3. Phases run start to end. A phase is 100% done (tested, merged) before the next one starts. Never do half and move on.
4. Work runs as 4-5 parallel Claude cloud sessions on the same GitHub repo, one branch each, merged into `main` per phase.
   The main (local) session writes every prompt.
5. The final git repo has NO .md files except `README.md` and `HACKATHON.md`. README = a solid open-source product README
   with images and GIFs that explains things. Build docs (this file, PLAN, PROGRESS, prompts, facts) leave git at the end.
6. Landing page shouts like a real product: WHAT it is and why you want it, not HOW it works. Not a tech page.
7. The video (Loom) is the very last step, after everything is finished.

## Read in this order
1. `PLAN.md` — architecture, graph schema, learning loop, proof, phases, who owns which folder.
2. `PROGRESS.md` — live state (main session writes it). Cloud sessions write ONLY `docs/progress/<session>.md`.
3. Your session's prompt in `prompts/phase<N>/`.

## Rules (all agents)
- **Stay in your folder.** Each part owns its directories (PLAN.md section 7). `src/pinned/contract.py`, `pyproject.toml`
  and `uv.lock` belong to the main session (frozen during a phase; ask under "Needs from main" in your progress file).
- **No fabrication.** Every number in README, UI or posts comes from a real run saved under `evidence/`. Unverified =
  say so. Never hand-enter per-problem facts; never let the graph see GitChameleon solutions or hidden tests.
- **Train/test split is sacred** (`evidence/split.json`, made once by part E). Learning only from the train split;
  every curve point is measured on the held-out split.
- **HydraDB is the only graph store.** No NetworkX/SQLite twin for the graph. A plain key-value store is allowed ONLY
  as the ablation arm in part D.
- Keys only in `.env` (git-ignored), never printed. The product needs no model or key; the eval solver is Rohit's own `claude -p` (no OpenRouter).
- **Git:** Rohit approved (15 Nov) that Claude does git + GitHub (`gh`). Repo `rohit-jsfreaky/pinned`, PRIVATE until the
  final clean-up phase. Cloud sessions: work on your own branch, commit and push it; never push to `main` (the main
  session merges). No nested `.git`. Official init commands only (`uv init --vcs none`, `npx create-next-app --skip-git`).
- Latest stable versions for new installs. Python via `uv`.
- Cloud sessions: 4 vCPU / 16 GB RAM / 30 GB disk, Docker, full network. Rohit's laptop: 12 GB RAM, overheats under
  long heavy load — heavy jobs (torch venvs, big sweeps) never run on the laptop.
- Talk to Rohit in simple English. Short sentences. Decide, don't ask, unless it costs money or is outward-facing.
- Product-grade only: no temp hacks. If you hit a HydraDB limit, work around it properly and record it (cloud sessions:
  in your progress file; the main session moves it into `docs/hydradb-findings.md`; we file real upstream issues).
