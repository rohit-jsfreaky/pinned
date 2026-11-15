# HydraDB v0.4.0 findings (Pinned build, 15 Nov 2026)

Image `ghcr.io/hydra-db/hydradb:0.4.0` = `sha256:ab71d651fefee13ebb23eb56d72316065bdca563ff7587442e07be663f2bf880`
(same digest as `latest` on 15 Nov). Upstream guide used: `USING-HYDRADB.md` at commit `916637477f3f`.
Every item was reproduced on a local dev node through `src/pinned/graph/client.py`. Numbers: `evidence/load/`.
"Upstream" = checked against all 100 most recent issues on hydra-db/hydradb on 15 Nov. NEW = not filed yet (Rohit files).

## NEW — worth filing

### 1. Guide says `UNWIND … MATCH … SET` is rejected; on v0.4.0 it works for relationship properties (and is the only fast way)
USING-HYDRADB.md ("Batch writes"): "There is no batch shape that writes properties: `UNWIND … MATCH … SET` is rejected
with `UNWIND MATCH must end in RETURN or DELETE`."
Reproduced the opposite:
```cypher
UNWIND $rows AS row MATCH (a {id: row.s})-[r:PQ]->(b {id: row.d}) SET r.w = row.w, r.ev = row.ev
```
with 1000 rows → success in 178 ms; all 1000 edges read back with the right `w` and `ev` (0 mismatches).
Impact: a reader of the guide concludes edge properties need one request per edge. Measured (evidence/load):
single `MERGE (a)-[:R {..}]->(b)` per edge = 44.8 edges/s; batch create + batch `MATCH … SET` = 2,466 edges/s.
Ask: document this shape (or, if it is not meant to be supported, say so and give the supported batch path).
Our workaround = we use it: `pinned.graph.link_props_many` / `set_edges_many`.

### 2. Single-statement writes are ~22 ms each and do not scale with concurrent clients
One `MERGE (a {id})-[:R {w: ..}]->(b {id})` per request: 44.9 edges/s with 1 client, 79.4 with 8, 74.9 with 32
(first bench run, 15 Nov). Reads on the same node scale fine (~1,100 qps at 8 clients). So writes look serialized
per commit. Ask: document the expected write throughput per shape, or allow multi-row relationship upserts with
properties in one statement. Workaround: batch shapes only (#1).

### 3. `count(<node binding>)` fails with an unrelated message
```cypher
MATCH (p:Page) RETURN count(p) AS n
```
→ `property values support integer, float, boolean, and string literals`. `count(*)` and `count(p.id)` work.
Ask: support `count(n)` or say "count() takes `*` or a property". Workaround: `count(*)`.

### 4. Node batch needs every field on every row, and gives no null
`UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Term, n.key = row.key, n.word = row.word` with one row lacking
`word` → `UNWIND row 1 is missing field word`. JSON `null` in a row is also not usable as "unset".
Workaround: `upsert_nodes` groups rows by their field set (one request per group). (Related but different: #186.)

## Already filed upstream (we hit them; no new issue)
- #188 `algo.SSpaths` with `pathCount: 0` stops after the shortest paths. We pass `pathCount: <limit>` (`ops.reach`).
- #130 `CONTAINS` / `ENDS WITH` in WHERE unsupported (`STARTS WITH` works). We match exact keys / prefixes.
- #281 list-valued properties (`field tags must be scalar`). We store scalars only.
- #116 no batch path for labelled nodes — partly stale: `UNWIND … MERGE (n {id}) SET n:Label, n.p = row.p` works now.
- #186 a missing property is `false` in WHERE, so `NOT (x.p = v)` matches nodes WITHOUT p. Never negate a property
  that can be absent; write the property on every edge/node that a filter reads.
- #177 the image never sets `RUST_MIN_STACK` (our run script sets `RUST_MIN_STACK=33554432`).
- #108 container runs as UID 10001 → host-folder writes fail; we `chmod 777` the data dir and delete it via a root
  container (`infra/hydradb-rm-data.sh`).

## Documented limits we design around (not issues)
- Plain single `CREATE (a)-[:R]->(b)` duplicates the edge on every run → we only use `MERGE` and `UNWIND` batch forms.
- Batch cap 1024 items → chunks of 1000. Pagination: resend `page_size`; a page fetch is consuming (not retryable)
  → `HydraGraph.query` restarts the whole scan on failure.
- Every MATCH needs an id/label/property anchor; one relationship type per pattern; procedures take several types.
- Procedure maps reject list parameters → `MSpaths` values inlined with `cypher_strings` (escaped).
- No `RETURN n` / `RETURN path`; no expressions in RETURN → compute client-side.
- The `:v0.4.0` tag does not exist on ghcr; `:0.4.0` does.
