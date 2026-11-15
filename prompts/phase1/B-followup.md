One more step after your final push. Save the full graph you built so nobody has to re-ingest it:

1. `git fetch origin main && git checkout origin/main -- infra/hydradb-snapshot.sh` (a tested save/load script from main).
2. `infra/hydradb-snapshot.sh save <your-node-name> data/pinned-graph.tgz` (it stops the node, archives it, starts it again).
3. Record in docs/progress/B-ingest.md: the file size, its sha256 (`sha256sum`), and the graph size it holds
   (Api nodes + edges per type from evidence/ingest/summary.json).
4. Upload it as a GitHub release asset on this repo:
   `gh release create graph-2026-11-16 data/pinned-graph.tgz --repo rohit-jsfreaky/pinned --title "Pinned graph" --notes "Full ingest of the 81 GitChameleon (library, version) pairs + ladders. sha256: <hash>"`
   If `gh` or the upload is blocked here, instead split it into 90 MB parts
   (`split -b 90m data/pinned-graph.tgz data/pinned-graph.tgz.part-`), add them on a NEW orphan branch `graph-snapshot`
   (only those part files + a SHA256SUMS file), push that branch, and write which way you used in your progress file.
5. Commit and push the progress-file change on your branch. Last message: where the graph file is + its sha256.
