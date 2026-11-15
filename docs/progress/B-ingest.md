# Phase 1 · B ingest — progress

Branch: `claude/adoring-noether-1irw8x`. Owner folders: `src/pinned/ingest/`, `tests/ingest/`, `evidence/ingest/`.
Export: `pinned.ingest.PyPIIngestor(graph, cache_dir=...)` implements `Ingestor` (`ingest_version`, `fill_gap`).

## What is done (per scope item)

1. **Pairs.** `dataset.py` downloads ONLY `dataset.jsonl` with `hf_hub_download(..., repo_type="dataset")` at a pinned
   commit (`799a6a33e572a07a8985914e7251f5dea54b0ac4`) into `data/gitchameleon/`. Reads only `library`, `version`,
   `python_version`, `additional_dependencies`. 81 pairs, 26 libraries, 328 problems → `evidence/ingest/pairs.json`.
2. **Version ladders.** `pypi.py` + `ladder.py`. PyPI JSON API, final releases only (no pre/dev/post). Ladder = every
   exact dataset version + the latest live patch of every minor from one minor below the oldest needed to one minor
   above the newest. Loose dataset versions ("2", "1.13", "4.28") are matched to the PyPI release they equal; the
   Version node keeps the dataset spelling (`pandas|2`, `ord` = ord of 2.0.0) so every part can look it up by the
   problem's own string. One dataset version is yanked on PyPI (networkx 2.6): it is kept (flag `yanked`) because the
   dataset pins it; yanked releases are never picked as "latest patch". 282 versions → `evidence/ingest/ladders.json`.
   Graph: `Library{name, import_names}` (import names from the wheel's top-level packages), `Version{library, version,
   ord, released, pypi, needed, ingested, apis, source_file, dynamic_python}`, `HAS_VERSION`, `NEXT`.
3. **Public API per version.** Wheel choice: pure-python > manylinux x86_64 CPython (dataset Python first) > sdist.
   Only `.py`/`.pyi` are extracted (path-traversal safe), the archive is deleted. Extension modules are recorded from
   the wheel (tagged `.so`, or a plain `.so` that exports `PyInit_<name>`; vendored libs like `scipy.libs/*.so` and
   ctypes libs like `lib_lightgbm.so` are not modules).
   - Static: griffe `GriffeLoader(allow_inspection=False)` (no foreign code runs in our process), aliases resolved.
     Public = from each public import name: non-underscore submodules (test packages skipped); module members via
     `__all__`, else names defined in the module plus re-exports in a hub (`__init__` or `api` module); imports of
     other projects never; class members incl. inherited ones from the same distribution.
   - Dynamic: planned for public modules that import a compiled module, have a re-export griffe cannot resolve, or
     build `__all__` at runtime. A throwaway `uv` venv per release on the dataset's Python (3.7 via `uv@0.5.31`, see
     limits), the exact release + the dataset's `additional_dependencies`, then `inspect_worker.py` runs griffe's
     dynamic inspection there (`griffe.inspect`, or `griffe.agents.inspector.inspect` on griffe 0.x for 3.7) and
     the venv is deleted. Install attempts, most faithful first: dataset pins + everything else as of release date
     (`--exclude-newer` release+120 days, pins exempt), pins alone, as-of-date alone, plain; wheels only on every
     candidate Python first, source builds last; torch/kymatio use the PyTorch CPU index. A failed import moves to the
     next attempt in a fresh venv. Signatures of C builtins / numpy ufuncs come from `inspect.signature` or the
     docstring's `name(params)` line (also wrapped and `a.reshape(...)` forms). Module-level instances of the
     library's own classes get their methods (`scipy.stats.norm.logcdf`).
   - Per object: dotted path, kind (function|class|method|attr|module), signature (params + defaults, no
     annotations, `self` dropped), docstring first line (a leading signature line skipped), re-export target, and
     deprecation evidence: `warnings.warn(..., DeprecationWarning|FutureWarning|PendingDeprecationWarning)` and
     "deprecat*" helper calls, `@deprecated`-style decorators, `.. deprecated::` directives, module `__getattr__`
     warnings. A warning inside if/try/loop is `partial` (a parameter or branch), else `object`.
   - Snapshots are cached as JSON per release (`data/pypi-cache/snapshots/`), keyed by a format number and by the
     library's gap modules, so a diff never compares snapshots built by different rules.
4. **Diff → edges** (`diff.py`, `writer.py`), schema and props exactly as contract.py:
   `ADDED_IN{evidence, sig}` on first appearance and every re-appearance; `REMOVED_IN{evidence}`;
   `CHANGED_IN{kind, before, after, evidence}` with kind `signature` (both signatures known and different),
   `deprecated` (object-level deprecation evidence appears or its text changes), `behaviour` (kind changes, partial
   deprecations, un-deprecation); `REPLACED_BY{w=W_DEFAULT, evidence, since_ord}` from deprecation text ("use X
   instead", "renamed to X", "moved to X", "replaced by X", "in favour of X", `alternative=X`, ...) resolved to real
   Api paths (as written, `np.`/`pd.` expanded, relative to the class / module / package, under each import name;
   current snapshot then the next rung's) and from same-object moves (a removed module-level function/class whose name
   and kind appear elsewhere; members follow a moved class); `ALIAS_OF{evidence}` re-export → canonical public path
   (the definition when public, else the shortest public path); `MEMBER_OF` (member → parent, class → module, module →
   package); `Term-CUES{w=W_DEFAULT, source: name|doc}->Api` from name parts (dotted/snake/camel, import name and
   generic parts like `core` dropped) and docstring words (stopwords, reST roles removed, lower-case).
   Every CUES/REPLACED_BY edge carries `w`. All writes are `upsert_nodes` / `link_many` / `link_props_many`.
   Re-running a version adds nothing, and never resets learned weights: CUES / REPLACED_BY edges that already exist
   are not rewritten (checked in a test after setting `w` by hand).
5. **`fill_gap(library, version, hint)`.** Resolves the hint ("scipy.special.i1", "special.i1", "i1", or "module 'x'
   has no attribute 'y'") to a path and its deepest module; remembers that module for the library (relaxed walk: every
   own re-export counts; dynamic inspection forced); rebuilds the snapshots of v and its neighbours, recomputes the
   facts at v (prev → v) and at the next rung (v → next), and writes them. Paths of the module that still have no
   ADDED_IN at or before v get one at the oldest re-checked rung (evidence says older versions were not re-checked).
   If the hinted attribute does not exist, the module's own words about it (string literals like numpy's
   `__former_attrs__` that name it with removed/deprecated/instead) become a REMOVED_IN, plus a REPLACED_BY when they
   name an existing replacement. The report counts only facts that were not there before; a repeat call reports 0.
6. **CLI** `uv run python -m pinned.ingest`: `--all`, `--lib scipy --version 1.7.3`, `--ladder scipy`,
   `--gap scipy 1.7.3 scipy.special.i1`, plus `--pairs` (dataset + ladders only), `--summary` (recount from the graph),
   `--jobs N` (parallel snapshot prebuild), `--only`, `--no-dynamic`. One report JSON per version in
   `evidence/ingest/reports/<lib>/<version>.json` (IngestReport + edges/nodes written + load/write seconds).
7. **Tests** `tests/ingest/` (29): a fake library with three releases built in a tmp dir (`fakelib.py`) on the real
   HydraDB test node, plus pure unit tests. Covered: removed function, rename with a "use X instead" warning
   (CHANGED_IN deprecated + REPLACED_BY with w and since_ord), signature change, alias/re-export (ALIAS_OF) + members +
   cues, remove-then-re-add, same object moved between modules, compiled-member fallback (the real worker runs on a
   runtime tree that has the module the static tree lacks; only the venv install is replaced), idempotent re-run that
   keeps learned weights, fill_gap adding a runtime-only attribute (and a repeat adding nothing), fill_gap recording a
   removal message + replacement, reports; units for ladders, wheel extraction, deprecation scanning, replacement
   resolution, terms, signatures, namespace/TYPE_CHECKING/if-else re-exports, runtime `__all__` star imports,
   dynamic planning + merge, install attempts, diff, canonical paths, cue targets.
8. **Real run.** See "Numbers".

## How to run
```
uv sync
infra/ensure-docker.sh && docker pull ghcr.io/hydra-db/hydradb:0.4.0
infra/hydradb-up.sh pinned-b 18443 17687 19090
export HYDRA_URL=http://127.0.0.1:18443
uv run python -m pinned.ingest --all --jobs 3        # pairs, ladders, all 282 versions, summary + coverage
uv run python -m pinned.ingest --gap scipy 1.7.3 scipy.special.i1
uv run pytest tests/ingest -q
```
Disk: sources ~4 GB and snapshots ~2.5 GB under `data/pypi-cache/` (git-ignored); venvs are deleted after use and
installed with `--no-cache` (uv's cache reached 23 GB and filled the disk before that).

## Numbers (final run: all 282 versions on one fresh node `pinned-b`; evidence/ingest/summary.json)
`summary.json` adds up the per-version IngestReports in `evidence/ingest/reports/` (no graph scan): edge counts are
rows written per type (CUES / REPLACED_BY: only new edges), Api paths = distinct paths in each library's snapshots.
gradio, librosa and torch were written by three `--ladder` processes in parallel with plotly (same node, idempotent).
Totals: 282 versions, 1,012,306 Api nodes, 4,642,283 edges written,
1,530,895 node rows, 3735 s of graph writes = 1,653 rows/s.
Snapshot building (downloads, griffe, venvs) mostly came from the cache in this run; a cold build of all snapshots
with 3 processes took about 1 h (sympy alone ~15 min). Removed spurious signature changes from repr() addresses:
995 (`evidence/ingest/signature-address-check.json`).

| library | versions | Api paths | ADDED_IN | REMOVED_IN | CHANGED_IN | REPLACED_BY | ALIAS_OF | MEMBER_OF | CUES | write s | rows/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| django | 8 | 81,282 | 81,528 | 2,085 | 2,233 | 246 | 66,800 | 81,527 | 168,717 | 225 | 2,254 |
| falcon | 5 | 3,886 | 3,891 | 282 | 286 | 58 | 2,609 | 3,883 | 12,462 | 8 | 3,591 |
| flask | 10 | 1,161 | 1,163 | 165 | 87 | 53 | 772 | 1,162 | 6,465 | 6 | 2,242 |
| geopandas | 9 | 735 | 735 | 29 | 186 | 2 | 357 | 734 | 3,632 | 4 | 1,884 |
| gradio | 45 | 16,740 | 16,787 | 3,875 | 5,759 | 237 | 19,544 | 16,786 | 21,181 | 137 | 903 |
| jinja2 | 6 | 2,980 | 2,990 | 223 | 103 | 48 | 1,698 | 2,989 | 9,378 | 7 | 3,250 |
| kymatio | 2 | 1,190 | 1,190 | 98 | 31 | 5 | 713 | 1,189 | 4,552 | 1 | 6,703 |
| librosa | 9 | 566 | 566 | 59 | 556 | 33 | 308 | 565 | 3,106 | 9 | 852 |
| lightgbm | 10 | 860 | 863 | 95 | 158 | 2 | 588 | 862 | 2,657 | 3 | 2,072 |
| matplotlib | 13 | 58,291 | 58,385 | 21,062 | 11,045 | 8,706 | 50,523 | 58,342 | 84,932 | 229 | 1,771 |
| mitmproxy | 7 | 10,338 | 10,338 | 3,770 | 146 | 339 | 3,428 | 10,336 | 61,570 | 43 | 2,625 |
| networkx | 11 | 5,359 | 5,361 | 867 | 793 | 63 | 3,651 | 5,360 | 21,307 | 20 | 2,329 |
| nltk | 6 | 25,600 | 25,600 | 235 | 285 | 118 | 20,979 | 25,595 | 58,488 | 57 | 2,891 |
| numpy | 9 | 40,703 | 40,711 | 9,706 | 1,345 | 5,549 | 35,639 | 40,650 | 40,765 | 95 | 2,510 |
| pandas | 9 | 28,484 | 28,500 | 8,611 | 7,287 | 3,141 | 22,462 | 28,499 | 63,222 | 89 | 2,436 |
| pillow | 5 | 4,891 | 4,892 | 32 | 159 | 1 | 3,356 | 4,891 | 10,855 | 10 | 2,966 |
| plotly | 42 | 173,356 | 173,747 | 12,033 | 11,470 | 261 | 203,149 | 173,743 | 215,598 | 789 | 1,354 |
| pytest | 7 | 1,055 | 1,055 | 309 | 25 | 218 | 542 | 1,053 | 2,686 | 4 | 2,038 |
| scikit-learn | 8 | 7,531 | 7,531 | 234 | 848 | 12 | 4,544 | 7,514 | 25,167 | 27 | 2,175 |
| scipy | 11 | 12,774 | 12,805 | 1,033 | 1,625 | 218 | 8,941 | 12,756 | 40,824 | 46 | 2,230 |
| seaborn | 5 | 619 | 619 | 216 | 145 | 133 | 294 | 618 | 2,531 | 2 | 2,724 |
| spacy | 4 | 24,735 | 24,735 | 684 | 320 | 90 | 19,875 | 24,609 | 35,833 | 36 | 3,829 |
| sympy | 11 | 372,146 | 372,343 | 31,727 | 17,238 | 4,359 | 365,741 | 372,341 | 246,336 | 1162 | 1,631 |
| torch | 13 | 134,625 | 135,537 | 27,296 | 14,661 | 6,860 | 107,529 | 135,532 | 282,261 | 714 | 1,305 |
| tornado | 11 | 2,150 | 2,153 | 440 | 435 | 8 | 918 | 2,151 | 10,818 | 11 | 1,916 |
| tqdm | 6 | 249 | 249 | 0 | 9 | 0 | 146 | 248 | 464 | 1 | 2,462 |
| **total** | 282 | 1,012,306 | 1,014,274 | 125,166 | 77,235 | 30,760 | 945,106 | 1,013,935 | 1,435,807 | 3735 | 1,653 |

**Coverage diagnostic was skipped for time in this final run** (no graph scan). An earlier full run (same loader
except the last two fixes) measured 191 of 210 scorable dataset `api_calls` valid at the problem's version.

## Saved graph
`infra/hydradb-snapshot.sh save pinned-b data/pinned-graph.tgz`: 3,437,492,110 bytes (3.3 GB),
sha256 `aced2bde35f64d22a8b50856b7ee196ab56da5a53f071cd07951642259ac84f5`. It holds the numbers above
(1,012,306 Api nodes, 76k Term nodes, 282 Versions, 26 Libraries; edges per type as in the table).
Over GitHub's 2 GB per-asset limit, so it is uploaded as parts (see the last line of this section).
Load: `cat pinned-graph.tgz.part-* > pinned-graph.tgz && infra/hydradb-snapshot.sh load pinned-graph.tgz <node> <http> <bolt> <admin>`.
**Upload: GitHub release creation is blocked for this session type (`HTTP 403: Creating, editing, or deleting releases is not permitted for this session type`), so the fallback was used:** 37 parts of 90 MB (`pinned-graph.tgz.part-aa` ... `-bk`) + `SHA256SUMS` on the orphan branch `graph-snapshot` (pushed in 4 batches; GitHub caps one push at 2 GB). Get it: `git fetch origin graph-snapshot && git worktree add /tmp/g origin/graph-snapshot && cat /tmp/g/pinned-graph.tgz.part-* > pinned-graph.tgz && sha256sum pinned-graph.tgz`.

## HydraDB findings (exact query, exact error, workaround; all reproduced in this session on `ghcr.io/hydra-db/hydradb:0.4.0`)

1. **Reads over 100,000 result rows are rejected, even when paged.**
   `MATCH (a)-[r:CUES]->(b) WHERE a.id IN $ids RETURN a.id AS src, b.id AS dst` with 1,000 Term ids (via
   `pinned.graph.edges_from`) → `resource_exhausted: query_result_rows rejected by admission control: actual 123766
   exceeds limit 100000`. The client pages (page_size 1000), but the limit counts the whole result.
   Workaround: anchor on the side with few edges (the new Api ids, ~10 cues each) and query 500 ids at a time
   (`writer._existing`). Common words ("get", "data") have 100k+ CUES out-edges, so source-side reads cannot work.
2. **Reads that walk over 250,000 edges are rejected, aggregates included.**
   `MATCH (t:Term)-[:CUES]->(a) RETURN count(*) AS n` → `resource_exhausted: cypher_edge_rows rejected by admission
   control: actual 250001 exceeds limit 250000`. An id-range filter does not narrow the walk:
   `MATCH (t)-[:CUES]->(a:Api) WHERE a.id >= $lo AND a.id < $hi RETURN count(*)` over 1/64 of the id space is still
   rejected (after 18 s). Equality on `library` narrows it, but a whole library (sympy, plotly, torch) is too big.
   Workaround: count per chunk of 1,000 known ids (`summary.library_counts`).
3. **Label scans hit limits at ~1M nodes.** `MATCH (a:Api) RETURN count(*) AS n` → `resource_exhausted:
   cypher_vertex_label_index_candidates rejected by admission control: actual 250001 exceeds limit 250000`; and once
   (right after the bulk load) `MATCH (v:Version) WHERE v.library = $l RETURN count(*) AS n` → `query_timeout:
   cypher_vertex_metadata exceeded query timeout after 30001 ms; limit is 30000 ms` (the same query took 0.08 s a few
   minutes later). `MATCH (t:Term) RETURN count(*)` takes ~17 s at 76k Terms. Workaround: look nodes up by id
   (`MATCH (v) WHERE v.id IN $ids`, ~0.01 s); ids are computed locally (`node_id`), so no label scan is needed.
4. **A label on an id-anchored read makes it ~100x slower.** On a 136k-node graph,
   `MATCH (a:Api) WHERE a.id IN $ids RETURN count(*)` (1,000 ids) took 10.9 s; `MATCH (a) WHERE a.id IN $ids ...`
   took 0.09 s. Looks like the label is scanned first and the id list applied as a filter. Workaround: no label in
   id-anchored reads (ids already encode the label).
5. **`UNWIND` before a read MATCH is not supported.** `UNWIND $ids AS i MATCH (a {id: i}) RETURN count(*) AS n` →
   `invalid_request: OpenCypher query is not supported yet: row execution currently supports MATCH/WITH clauses
   followed by RETURN`. Workaround: `WHERE a.id IN $ids`.
6. Design note (not a bug): an edge is unique per (source, type, target), because the batch create in
   `link_many` dedups. Two changes of one API at one version (signature AND deprecation) therefore share one
   `CHANGED_IN` edge; see "Needs from main".

## Needs from main
- **Contract note for CHANGED_IN:** when several changes of one Api happen at one version, the edge's `kind` is the
  most important (signature > deprecated > behaviour) and the others are in `extra_kinds` (comma-joined) with their
  text in `extra` ("kind: before -> after || ..."). Search (C) should also read `extra_kinds` when it decides
  "deprecated". Every CHANGED_IN edge carries both props (empty string when there is nothing), so filters are safe.
- **Extra node props (documented here, no contract change needed):** Version: `pypi` (exact PyPI release),
  `needed` (a problem asks for it), `ingested`, `apis`, `source_file`, `dynamic_python`. Api: `doc` (first docstring
  line from the version that first added it). Version keys use the dataset's spelling for needed versions
  (`pandas|2`, `torch|1.13`, `jinja2|2.11`); match by `ord` (or `Version()` equality) when a caller has another
  spelling.
- **Move the HydraDB findings above into `docs/hydradb-findings.md`.** #1-#4 look worth filing (admission limits are
  not in USING-HYDRADB.md as far as I can see; #4 is a planner issue).
- **Graph helper suggestion (part A):** `edges_from` reads out-edges by SOURCE ids; for hubs (Terms) that trips #1.
  An `edges_into(g, dst_ids, rel)` helper (target-anchored, no label) would help C and D too. I did not edit
  `pinned.graph`; ingestion uses `HydraGraph.query` with that shape.
- No new dependency was needed. Python 3.7 comes from `uvx uv@0.5.31 python install 3.7` (current uv no longer ships
  3.7); `infra/cloud-setup.sh` could pre-install it.

## Known limits (honest)
- **Size comes from inheritance.** Inherited members are real API (`sympy.Symbol.subs`), so each subclass and each
  re-export path gets its own Api node (sympy ~290k paths per version). CUES go only to definitions and the shortest
  path of each object (ALIAS_OF links the copies), so a word does not fan out to 1,000 copies. A namespace change
  moves many inherited copies at once, which inflates REMOVED_IN / REPLACED_BY counts (numpy 1.22 dropped the scalar
  types from `numpy.core`: ~70 method paths per type).
- **Dynamic inspection gaps (each recorded in the snapshot):** librosa 0.5.1/0.6.0 (its 2018 dependency chain
  resampy → numba → llvmlite 0.23 has no Python 3.7 wheels and does not build; librosa is pure Python, so only two
  stdlib names leaking through a star import were left to check), kymatio (the planned modules are its TensorFlow /
  Keras frontends; TensorFlow is not in the dataset's dependencies), single optional modules (matplotlib tornado/wx
  backends, caffe2 protobuf, torch `functorch.dim` on 1.13+, numpy `core.umath_tests`, Django `contrib.gis` without
  GDAL). Rungs no problem asks for get no `additional_dependencies` (e.g. torch 1.9.1 imports without numpy).
- **Re-export rule:** a plain module's `from x import y` is not counted as API (otherwise `pandas.io.sql.DataFrame`
  and thousands like it). Real re-export shims written that way (`falcon.uri.parse_query_string`,
  `matplotlib.pyplot.style`) are left to `fill_gap`, whose relaxed walk adds them when learning reports the failure.
  griffe resolves a name shared by a function and a submodule to the module (`geopandas.tools.sjoin`), so
  `geopandas.sjoin` is missing statically.
- **Deprecation / replacement extraction is pattern-based**: messages built at runtime only keep their literal parts;
  replacements are resolved inside the same library only; "same object moved" uses name + kind (+ target), at most 5
  candidates.
- **Signatures:** static ones keep the source text of defaults, dynamic ones use `repr()`; a path that flips between
  static and dynamic across versions can show a signature change that is only formatting.
- **fill_gap** re-checks v and its neighbours only; a gap path that also existed in older rungs is anchored at the
  oldest re-checked rung (the evidence says so); `--ladder <lib>` after a gap re-loads every rung with the gap module.
- **Version ordering** uses `version_ord`; post releases are skipped (they would share an ord with their base).
- griffe does not load implicit namespace subpackages (folders without `__init__.py`, e.g. `flask/sansio` in 3.0);
  an empty `__init__.py` is added to the extracted copy so their classes resolve as bases.
- griffe flags everything inside a plain `if/else` as not-runtime (numpy's whole top level), so that flag is not
  used; `if TYPE_CHECKING:` / `if t.TYPE_CHECKING:` imports are detected from the AST instead.

