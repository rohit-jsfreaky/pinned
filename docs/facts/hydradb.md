# HydraDB facts for this build (re-verified 15 Nov 2026, Phase 0)

- Engine: `hydra-db/hydradb` **v0.4.0** (14 Nov 2026), ELv2, Rust. Image **`ghcr.io/hydra-db/hydradb:0.4.0`**
  (digest `sha256:ab71d651…bf880`; `:v0.4.0` does not exist). Ports: HTTP query 8443 (`/healthz`), Bolt 7687,
  admin 9090 (`/readyz` = 200 with empty body, `/metrics`).
- The authoritative usage guide is upstream `USING-HYDRADB.md` (read it if you write raw Cypher). Our corrections to it
  and every limit we hit: `docs/hydradb-findings.md`.

## Run a node
`infra/hydradb-up.sh <name> <http> <bolt> <admin>` (data in `.hydradb/<name>`, git-ignored);
remove its data with `infra/hydradb-rm-data.sh <name>` (the container owns the files).
Tests need no manual node: the `graph` fixture in `tests/conftest.py` starts a throwaway node on free ports (or uses
`HYDRA_URL` if set) and removes it after the session. Use the `run_key` fixture to prefix keys.

## Use the graph layer, not raw HTTP — `from pinned.graph import ...`
| helper | what it does | shape on the server |
|---|---|---|
| `HydraGraph(url=...)` | pooled, thread-safe client; `query()` returns ALL pages as dicts; `write()`; `.requests` counts round trips | HTTP JSON |
| `upsert_nodes(g, label, rows)` → ids | rows need `key` + scalar props; id = `node_id(label, key)`; also stores `key` | `UNWIND … MERGE (n {id}) SET n:L, n.p=row.p` |
| `upsert_node(g, label, key, **props)` | one node | same |
| `link_many(g, rel, [(src, dst)])` | property-less edges, idempotent | `UNWIND … CREATE (a {id})-[:R]->(b {id})` |
| `link_props_many(g, rel, [(src, dst, props)])` | edges WITH props, batched, idempotent | batch create, then `UNWIND … MATCH … SET r.p=row.p` |
| `set_edges_many(g, rel, rows)` / `set_edge(...)` | update props of existing edges (absent = skipped) | `MATCH … SET` |
| `link(g, src, rel, dst, props)` | one edge with props (slow: ~45/s — never in loops) | `MERGE` |
| `edges_from(g, ids, rel, props=(..), dst_props=(..))` | out-edges of many sources in one request | `WHERE a.id IN $ids` |
| `node(g, label, id, props)` | read one node's props (None if absent) | label-anchored MATCH |
| `paths(g, src, dst, rels, max_len, count)` | `algo.SPpaths`; `max_len` counts EDGES | |
| `reach(g, src, rels, max_len, limit, direction)` | `algo.SSpaths`, every simple path (limit caps the total) | |
| `multi_paths(g, label, sources, targets, rels, ...)` | `algo.MSpaths` by `key` strings, many seeds in ONE request | |
| `count_nodes`, `count_edges`, `delete_nodes`, `unlink` | | |
| `node_id(label, key)`, `version_ord("2.0.0")` | stable 63-bit ids; sortable version int (2.0.0 → 2_000_000_999) | |
Paths come back as `Path(nodes=[{id, labels, **props}], rels=[{type, src, dst, **props}])`.

## Measured on the laptop (evidence/load/*-synthetic.json; 22k nodes, 46k edges)
Writes: nodes 26.8k rows/s · edges 8.5k rows/s · edges with props 2.5k rows/s · single MERGE 45/s.
Reads p50 at 1 client: validity of 8 APIs 7.2 ms · replacement chain (SSpaths) 2.9 ms · top-20 CUES 3.6 ms ·
MSpaths 8×8 6.1 ms; ~1,100 qps at 8 clients, 0 errors at 32.

## Cypher rules that bite (full list in USING-HYDRADB.md + docs/hydradb-findings.md)
- Every MATCH needs an id, label or property anchor (`WHERE a.id IN $ids` counts). One rel type per pattern; the
  `algo.*` procedures accept several. Variable-length `[:R*1..3]` needs a fixed source id and revisits nodes.
- `RETURN` = `x.prop AS name` and aggregates (`count(*)`, `count(x.p)`, `sum`, `avg`, `collect`) only; no
  expressions, no `RETURN n`, no `count(n)`, no `count(DISTINCT ..)`.
- WHERE: `= <> < > <= >= AND OR NOT`, `IN $list`, `STARTS WITH`. No `CONTAINS`/`ENDS WITH`.
- A missing property is `false` in WHERE (upstream #186): never write `NOT (r.p = v)` on a property some edges lack.
- Properties are scalars only (no lists). Procedure maps take scalar params only.
- Plain single `CREATE` duplicates edges — use the helpers (all idempotent).
- Auto-commit only, no transactions.
