#!/usr/bin/env bash
# Build the static site from content/ into _site/, then index it for search.
#   ./build.sh            build
#   ./build.sh serve      build, then serve on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

BUILD_SH=1 "$PY" build/generate.py
npx -y pagefind@1.3.0 --site _site

if [ "${1:-}" = "serve" ]; then
  echo "Serving on http://localhost:8000"
  "$PY" -m http.server 8000 --directory _site
fi
