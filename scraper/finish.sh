#!/usr/bin/env bash
# Wait for the running crawl, then fetch whatever content/ still lacks, then rebuild.
cd "$(dirname "$0")/.."
while pgrep -f "crawl.py fetch" >/dev/null; do sleep 30; done
.venv/bin/python build/extract.py
cp content/missing-media.txt archive/priority.txt
python3 -u scraper/crawl.py fetch --only-priority
.venv/bin/python build/extract.py
./build.sh
.venv/bin/python build/checklinks.py
echo "[$(date +%H:%M:%S)] finish.sh done"
