# PLAN — Pinned (15 Nov 2026; phases rewritten after Phase 0)

## 1. The product in one breath
Coding agents write code for the wrong library version. Pinned is a HydraDB graph of every public API across library
versions (added / removed / renamed / signature changed / replaced by), plus what it learned from real runs. Ask it
"I'm on scipy 1.7.3, how do I do X?" or "check this code against my versions", and it walks the graph to the call that
is valid for YOUR version, with the chain as evidence. Every pass/fail of an agent's code updates the graph, so it gets
sharper with use. Shape borrowed from Precedent Graph ("is this case still good law?") and Canon/Hindsight ("what is
still true now") — the winning type of the last two graph hackathons.

## 2. Architecture
```
PyPI wheels/sdists ──(B ingest, griffe static load, version ladder)──┐
                                                                      ▼
agent / MCP / web ──(F)──> Search (C) ──Cypher──> HydraDB (A) <──Cypher── Learner (D) <── Episodes (pass/fail + error)
                                                     ▲                         │ gap requests ─────> Ingestor (B)
GitChameleon 2.0 harness (E): split · solver (claude -p) · venv runner · arms · curve · controls ─────┘
```
Everything talks to the graph through `pinned.graph` (client + helpers, HydraDB 0.4.0; usage table in
`docs/facts/hydradb.md`). Shared types, schema, weights and the validity rule are in `src/pinned/contract.py` (frozen).
The product has no model inside: the user's own agent calls Pinned over MCP (`check_code`, `how_to`, `explain_api`,
`report_outcome`); `report_outcome` feeds the same Learner the benchmark uses, so real use sharpens the graph too.

## 3. Graph schema (contract.py has the constants)
Nodes (`id` = `node_id(label, key)`):
- `Library {name}` key `pandas` · `Version {library, version, ord, released}` key `pandas|2.0.0` (`ord` = sortable int
  from packaging.version, e.g. 2.0.0 -> 2000000000) · `Api {library, path, kind}` key `pandas|pandas.DataFrame.append`
  (identity across versions) · `Term {word}` · `Episode {problem_id, library, version, passed, error_type, iteration}`.
Edges: `Library-HAS_VERSION->Version`, `Version-NEXT->Version`, `Api-ADDED_IN->Version`, `Api-REMOVED_IN->Version`,
`Api-CHANGED_IN{kind,before,after}->Version`, `Api-REPLACED_BY{w,evidence,since_ord}->Api`, `Api-ALIAS_OF->Api`,
`Api-MEMBER_OF->Api`, `Term-CUES{w,source}->Api`, `Term-SHORTCUT{w,lo_ord,hi_ord,hops}->Api`, `Episode-USED{helped}->Api`.
"Valid at version v": the latest ADDED_IN/REMOVED_IN event at or before v is ADDED_IN (exact rule in contract.py).

## 4. The learning loop (Track 1 — the heart; D implements, E drives)
Per iteration t on the TRAIN split:
1. For each train problem: `ctx = search.context_for(problem, starting_code, lib, ver)`; solver writes code with ctx;
   E runs it in that problem's venv -> `Episode(passed, error_type, error_text)`.
2. `learner.record(episode)`:
   - **edge reweighting**: CUES/SHORTCUT/REPLACED_BY edges behind context items the code used get +w on pass, -w on fail.
   - **gap-driven ingestion**: AttributeError / ImportError / unexpected-keyword TypeError naming X at version v, where the
     graph had no or wrong facts -> `ingestor.fill_gap(lib, v, X)` (deeper ingest of that module: members, re-exports,
     deprecation messages "use Y instead" -> REPLACED_BY edges).
   - new CUES edges from problem words to the API that finally worked (learned, never from the answer key).
3. `learner.maintain()` once per iteration:
   - **path compression**: a multi-hop chain (Term -> Api -REPLACED_BY-> Api -ALIAS_OF-> Api) that led to passes >= 2
     times becomes a direct `SHORTCUT` edge for that version range.
   - **entity merge**: two Api nodes that are the same object (re-export / moved module, griffe alias target) -> ALIAS_OF.
   - **entity split**: one path whose signature or behaviour changed across versions -> CHANGED_IN facts per version so
     the search answers per version, not per name.
   - **decay**: edges that keep leading to failures fall below a floor and stop being suggested.
4. E measures the HELD-OUT test split with the frozen graph -> one curve point (pass@1). No learning from test.
Iteration 0 = ingestion only (the static graph) = our main baseline. Iterations 1..N = learning.

## 5. Proof (E owns; every number lands in `evidence/`)
- Split: 50/50 stratified by library, seed 13, written once to `evidence/split.json`.
- Arms on the test split, same model, temperature 0, same prompt template:
  1. no context (the agent alone)
  2. docs: text of the problem's official `docs` URLs (truncated) — the "plain docs" baseline (Context7-like)
  3. static graph (iteration 0)
  4. **Pinned** after each learning iteration 1..N (the curve)
  5. ablation: the same learned signals kept in a plain dict (no traversal, no replacement chains) — "would it fall apart on
     a plain store?"
  6. negative control: Pinned context with shuffled edge weights / random items of the same size
- Also: query latency p50/p95 for `context_for` under 1/8/32 concurrent clients (A's load bench), graph size, ingest rate.
- Report per library and per `type_of_change`. Publish failures honestly.

## 6. Phases (Rohit's rule: a phase is 100% done and merged into `main` before the next starts)
Each phase = parallel Claude cloud sessions (4 vCPU / 16 GB / 30 GB disk, Docker, full network), one branch each,
started from `main`. A session owns its folders only, never edits `contract.py`, `pyproject.toml`/`uv.lock` (deps are
pre-added), `PLAN.md`, `PROGRESS.md` or `CLAUDE.md`, and writes its report to `docs/progress/<session>.md`.
The main (local) session reviews, merges every branch into `main`, runs the whole test suite, updates PROGRESS.md and
writes the next phase's prompts.

| phase | sessions | done when |
|---|---|---|
| 0 | main session (local) | DONE: graph layer (`pinned.graph`: hardened client, batched idempotent writes incl. edge props, paths/reach/multi_paths, counts), throwaway-node test fixture, 15 tests green, load bench + `evidence/load/`, `docs/hydradb-findings.md`, contract frozen, deps pre-added, repo on GitHub |
| 1 | B ingest · C search · D learn · E eval harness · F server+MCP (`prompts/phase1/`) | each part complete and tested on its own (fixture graphs; real PyPI data for B; real dataset + runner self-check for E) |
| 2 | integration + real data | full graph of all 81 (library, version) pairs + ladders on one node; search tuned on it; the learning curve (iterations 0..N) + every baseline, ablation, control, latency under load; `evidence/curve.json` + charts; RESULTS |
| 3 | website | **Rohit chooses the design first** (we ask, then build): product landing (what + why, not how), try-it, API timeline, curve page |
| 4 | deploy | live app + MCP endpoint on Rohit's box/domain, seeded graph |
| 5 | clean repo | only `README.md` + `HACKATHON.md` as .md; real OSS README with images/GIFs; posts drafted; repo public |
| 6 | video | Rohit records the Loom |

## 7. Ownership (folders)
| part | owns |
|---|---|
| A graph core (main session) | `src/pinned/graph/`, `infra/`, `tests/graph/`, `tests/conftest.py`, `bench/`, `docs/hydradb-findings.md` |
| B ingest | `src/pinned/ingest/`, `tests/ingest/`, `evidence/ingest/` |
| C search | `src/pinned/search/`, `tests/search/`, `evidence/search/` |
| D learn | `src/pinned/learn/` (incl. the plain-store ablation + shuffled control), `tests/learn/`, `evidence/learn/` |
| E eval | `src/pinned/eval/`, `tests/eval/`, `scripts/cloud/`, `evidence/split.json`, `evidence/runs/`, `evidence/harness/` |
| F product server | `src/pinned/server/`, `src/pinned/cli.py`, `tests/server/` (web/ and deploy/ come in phases 3-4) |
| main session | `contract.py`, `pyproject.toml`, `uv.lock`, PLAN/PROGRESS/CLAUDE, `docs/facts/`, README, merges |

## 8. Decisions already made
- Python 3.12, uv, httpx, griffe, FastAPI + uvicorn, official MCP Python SDK; Next.js for the web (official create-next-app).
- HydraDB image pinned to `ghcr.io/hydra-db/hydradb:0.4.0`. Tests get a throwaway node from the `graph` fixture.
- **The product has NO model inside** (like Dejo): the user's agent thinks; Pinned's search and learning are
  deterministic graph code. No API key needed to run Pinned.
- **The eval solver = Rohit's own Claude Code**, headless: `claude -p --model haiku` (one fresh session per problem, no
  tools, prompt = problem + starting code + the arm's context, output = code only). One model for every arm; record CLI
  version + model per run. Every completion cached on disk and saved with the run. Small concurrency, back off on rate
  limits, resume from cache. Phase 1 E finds out whether `claude -p` works inside a cloud session; if not, the solver
  step runs on Rohit's laptop and the test-runner step runs in the cloud, joined by the saved completions.
- GitChameleon answer key (`solution`, ground-truth solutions, hidden tests) never enters the graph or a prompt. The
  only allowed use: E's runner self-check (ground truth must pass in our venvs = proof the runner is right).
