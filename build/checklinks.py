#!/usr/bin/env python3
"""Report internal links/images in _site/ that don't resolve to a file."""
import os
import re
import sys
import urllib.parse as up
from collections import Counter

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_site")
REF_RE = re.compile(r'(?:href|src)="([^"#]*)(?:#[^"]*)?"')

broken = Counter()
pages = 0
for dp, _, fs in os.walk(SITE):
    if "/pagefind" in dp:
        continue
    for f in fs:
        if not f.endswith(".html") or f == "404.html":
            continue
        pages += 1
        page = os.path.join(dp, f)
        for ref in REF_RE.findall(open(page, encoding="utf-8").read()):
            if not ref or re.match(r"^[a-z]+:|^//", ref):
                continue
            target = os.path.normpath(os.path.join(dp, up.unquote(ref.split("?")[0])))
            if os.path.isdir(target):
                target = os.path.join(target, "index.html")
            if not os.path.exists(target):
                broken[os.path.relpath(target, SITE)] += 1
print(f"{pages} pages checked, {len(broken)} broken targets")
for t, n in broken.most_common(int(sys.argv[1]) if len(sys.argv) > 1 else 25):
    print(f"  {n:4}  {t}")
