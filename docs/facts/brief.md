# HydraDB Hackathon brief (pasted by Rohit from the HydraDB Discord, 15 Nov 2026 ~2:45 PM IST) — word for word

**@everyone the HydraDB Hackathon is live 👀**

build on the graph. one rule: you build on **HydraDB as your graph DB**.

**Track 1: the self-improving graph**
a graph search system that learns from its own answers to improve what it ingests and how it queries. sharper the more it's used.
fair game: path compression, edge reweighting, gap-driven ingestion, entity split/merge.
the bar: show an accuracy curve rising over iterations.

**Track 2: the agent context layer**
use HydraDB as the live context store for an agent: its skills, tool and MCP definitions, and the entities it already resolved. instead of preloading every tool into the window and fanning out into round-trip chains, the agent traverses the graph and pulls only what the task needs.

**how we judge** (both tracks, equal weight)
- **engineering**: real graph traversal, does it actually need the graph, holds up under load.
- **go-to-market**: who wants it, and can you pitch it as a product people pay for.

**prizes** — each track: a winner and a runner-up (never print amounts publicly).

## FAQ
**Q: Which HydraDB do we build on?** The open-source engine, `github.com/hydra-db/hydradb`, not the hosted platform. Run it
yourself and build on it as your graph DB. That's the one rule.
**Q: How is judging split?** Two equal halves: **technical implementation** and **go-to-market**.
**Q: What are you looking for in technical implementation?**
- **Right metrics**: did you measure the thing that actually matters, not a vanity number?
- **Real continual-learning improvement + a baseline**: define a clear baseline, then show genuine improvement over it (not a one-off result).
- **Impact of HydraDB and the graph**: is the graph doing real work? Would this fall apart on a plain store?
**Q: What's go-to-market?** Post about your build on Twitter, LinkedIn, and other socials, and tag the HydraDB account. Show
who wants it and that you can pitch it as a product people would pay for.
**Q: How do we submit?** 1. **Host your app** so we can actually use it (live link). 2. **Open-source GitHub repo** with the
code. 3. **A Loom** walking through what you built and how it works.
**Q: When's the submission cutoff?** 9 AM IST on Nov 16. Hard deadline.

## What won before (our studies)
- Hack Hydra Aug 2026: `~/my_projects/hackathon_ideas/research/winners-study/hydra-report.md` — winners had an OUTSIDE
  source of truth + a control that attacks their own claim, did work INSIDE HydraDB (engine patch / upstream issues), and
  matched the sponsor's theme "what is still true now".
- Graph Hacks (FalkorDB) Oct 2026: Precedent Graph won Track 1 with "is this case still good law?" — Pinned is that shape
  for code: "is this call still valid in my version?"
- Idea page: `~/my_projects/hackathon_ideas/contexts/hydradb-nov-2026.md` (section 10 + v5 pick).

## The field (GitHub, 15 Nov ~4:15 PM) — full list: ~/my_projects/hackathon_ideas/contexts/hydradb-nov-2026-FIELD.md
- Hackathon window is 14-16 Nov; ~23 public repos. Track 2 tool routers are the crowded cluster (~10). Track 1 rivals:
  baton (issue -> team, curve on 2,000 kubernetes issues by date), TrailMend (who to page), trailmark (code context that
  learns from merges), decisiongraph ("decision that still stands"), unstale, physarum, dream-cycle, supply-switch.
- Nobody is doing library-version validity. Held-out curves and live demos are COMMON, so ours must also have: an
  outside benchmark with executable tests (GitChameleon), the plain-store ablation, the negative control, and visible
  graph work (replacement chains), plus a strong live demo.
