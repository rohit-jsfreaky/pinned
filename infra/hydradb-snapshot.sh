#!/usr/bin/env bash
# Save / load a whole HydraDB graph as one .tgz (the node's store folder; verified to restore byte-for-byte).
#   infra/hydradb-snapshot.sh save <node-name> <out.tgz>    # stops the node, archives it, starts it again
#   infra/hydradb-snapshot.sh load <in.tgz> <node-name> <http> <bolt> <admin>   # new node serving that graph
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMG=ghcr.io/hydra-db/hydradb:0.4.0
case "${1:-}" in
  save)
    NAME=${2:?node name}; OUT=$(realpath -m "${3:?out.tgz}")
    [ -d "$ROOT/.hydradb/$NAME/store" ] || { echo "no data at .hydradb/$NAME" >&2; exit 1; }
    docker stop "$NAME" >/dev/null   # a consistent copy needs the node stopped
    # the container owns the files, so archive from inside a root container
    docker run --rm --user 0 --entrypoint sh -v "$ROOT/.hydradb/$NAME:/src:ro" -v "$(dirname "$OUT"):/out" "$IMG" \
      -c "tar -C /src -czf /out/$(basename "$OUT") store && chmod 644 /out/$(basename "$OUT")"
    docker start "$NAME" >/dev/null
    echo "saved $(du -h "$OUT" | cut -f1) -> $OUT"
    ;;
  load)
    IN=$(realpath "${2:?in.tgz}"); NAME=${3:?node name}
    [ ! -e "$ROOT/.hydradb/$NAME" ] || { echo ".hydradb/$NAME already exists; pick a new name" >&2; exit 1; }
    mkdir -p "$ROOT/.hydradb/$NAME"
    tar -C "$ROOT/.hydradb/$NAME" -xzf "$IN"
    "$ROOT/infra/hydradb-up.sh" "$NAME" "${4:?http port}" "${5:?bolt port}" "${6:?admin port}"
    ;;
  *) echo "usage: $0 save <node> <out.tgz> | load <in.tgz> <node> <http> <bolt> <admin>" >&2; exit 2 ;;
esac
