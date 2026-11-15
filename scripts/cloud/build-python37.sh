#!/usr/bin/env bash
# Build CPython 3.7.17 (the upstream harness's 3.7) into data/pythons/3.7.17. uv has no 3.7 builds.
# Needs the build deps from setup-eval.sh. Takes ~3 minutes on 4 vCPU.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PREFIX="$ROOT/data/pythons/3.7.17"
if "$PREFIX/bin/python3.7" -c 'import ssl, sqlite3, bz2, lzma, ctypes, zlib' 2>/dev/null; then
  echo "CPython 3.7.17 already at $PREFIX"; exit 0
fi
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
curl -fsSLO https://www.python.org/ftp/python/3.7.17/Python-3.7.17.tgz
echo "fd50161bc2a04f4c22a0971ff0f3856d98b4bf294f89740a9f06b520aae63b49  Python-3.7.17.tgz" | sha256sum -c -
tar xzf Python-3.7.17.tgz && cd Python-3.7.17
./configure --prefix="$PREFIX" --with-ensurepip=install >configure.log 2>&1
make -j"$(nproc)" >make.log 2>&1
make install >install.log 2>&1
"$PREFIX/bin/python3.7" -c 'import ssl, sqlite3, bz2, lzma, ctypes, zlib; print("CPython 3.7.17 ok,", ssl.OPENSSL_VERSION)'
