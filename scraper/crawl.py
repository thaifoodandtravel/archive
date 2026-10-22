#!/usr/bin/env python3
"""Mirror thaifoodandtravel.com from the Internet Archive Wayback Machine.

Usage:
    python3 scraper/crawl.py cdx        # fetch the capture indexes -> archive/cdx*.txt
    python3 scraper/crawl.py plan       # build archive/plan.tsv (deduped, filtered)
    python3 scraper/crawl.py fetch      # download everything in the plan (resumable)
    python3 scraper/crawl.py fetch --only-priority   # just the paths in archive/priority.txt
    python3 scraper/crawl.py discover   # find referenced-but-missing assets in fetched HTML, add to plan

Files land in archive/raw/<path>, mirroring the original site layout.
Progress is recorded in archive/manifest.jsonl; failures in archive/failures.tsv.
Only the standard library is used.

The Wayback Machine refuses connections (for tens of minutes) when crawled too
fast, and every redirect counts as a request. So we pick exact captures from the
CDX index up front -- one request per file -- and fetch them sequentially.
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "archive")
RAW = os.path.join(ARCHIVE, "raw")
CDX_FILE = os.path.join(ARCHIVE, "cdx.txt")            # one row per URL (first capture)
CDX_HTML_FILE = os.path.join(ARCHIVE, "cdx_html.txt")  # every distinct HTML capture
PLAN_FILE = os.path.join(ARCHIVE, "plan.tsv")
MANIFEST = os.path.join(ARCHIVE, "manifest.jsonl")
FAILURES = os.path.join(ARCHIVE, "failures.tsv")
PRIORITY_FILE = os.path.join(ARCHIVE, "priority.txt")  # optional: site paths to fetch first

DOMAIN = "thaifoodandtravel.com"
SITE_URL = f"http://www.{DOMAIN}"
CDX_BASE = "https://web.archive.org/cdx/search/cdx?url=" + DOMAIN + "/*&filter=statuscode:200"
CDX_QUERIES = {
    CDX_FILE: CDX_BASE + "&fl=timestamp,original,mimetype,statuscode,length&collapse=urlkey",
    CDX_HTML_FILE: CDX_BASE + "&filter=mimetype:text/html&fl=timestamp,original,digest,length&collapse=digest",
}
# Used only for discovered assets with no known capture (costs a redirect).
LATEST_TS = "20240301000000"
# Seconds between requests.
MIN_INTERVAL = 5.0
# Pause after the server refuses a connection.
BLOCK_PAUSE = 20 * 60
# A digest shared by this many distinct pages is a template (e.g. the 404 page).
TEMPLATE_DIGEST_MIN = 4
HTML_CANDIDATES = 3
UA = "Mozilla/5.0 (archival mirror of thaifoodandtravel.com for personal restoration)"

KEEP_TYPES = (
    "text/html", "image/", "application/pdf", "text/css",
    "application/rss+xml", "text/plain",
)
SKIP_PATTERNS = [
    r"/comment-page-\d+",
    r"/(feed|trackback|embed)/?$",
    r"/feed/",
    r"^/blog/(tag|category|author)/",
    r"^/blog/\d{4}/",              # date archives
    r"/page/\d+/?$",
    r"^/blog/wp-(login|admin|json)",
    r"^/blog/xmlrpc",
    r"^/cgi-bin/",
    r"^/wordpress/",
    r"\.html?/",                   # broken relative links like /x.html/y.html
]
SKIP_RE = re.compile("|".join(SKIP_PATTERNS))
LOW_PRIORITY_RE = re.compile(r"^/(travel|classes|trips?|tours)|^/[^/]*(trip|tour)[^/]*$", re.I)
SOFT_404 = re.compile(rb"requested page is not found|Error 404|Page Not Found", re.I)


def norm_path(url):
    """Normalize an archived URL to a site-relative path (no query/fragment)."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if host and not host.endswith(DOMAIN):
        return None
    path = urllib.parse.unquote(u.path or "/")
    path = re.sub(r"/{2,}", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    return path


def local_file(path):
    """Map a site path to a file under archive/raw."""
    p = path
    last = p.rsplit("/", 1)[-1]
    if p.endswith("/") or "." not in last:
        p = p.rstrip("/") + "/index.html"
    p = p.lstrip("/")
    # keep it filesystem-safe
    p = re.sub(r'[<>:"\\|?*\x00-\x1f]', "_", p)
    return os.path.join(RAW, p)


def wanted(path, original):
    return path is not None and not SKIP_RE.search(path) and "replytocom" not in original


def http_get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        # id_ captures are served as stored; some were stored gzip-encoded
        if body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
        return r.status, r.geturl(), r.headers.get("Content-Type", ""), body


# ---------------------------------------------------------------- cdx

def cmd_cdx():
    os.makedirs(ARCHIVE, exist_ok=True)
    for dest, url in CDX_QUERIES.items():
        if os.path.exists(dest):
            print(f"{dest} exists, skipping")
            continue
        for attempt in range(5):
            try:
                print(f"fetching {os.path.basename(dest)} ...", flush=True)
                _, _, _, body = http_get(url, timeout=600)
                text = body.decode("utf-8", "replace")
                if "<html" in text[:200].lower():
                    raise RuntimeError("CDX returned an HTML error page")
                with open(dest, "w") as f:
                    f.write(text)
                print(f"wrote {dest}: {text.count(chr(10))} rows")
                break
            except Exception as e:
                print(f"  attempt {attempt + 1} failed: {e}")
                time.sleep(60 * (attempt + 1))
        else:
            sys.exit("could not fetch CDX index")
        time.sleep(MIN_INTERVAL)


# ---------------------------------------------------------------- plan

def html_captures():
    """{local file: [(ts, original), ...] newest first}, excluding template/404 captures."""
    caps = defaultdict(list)
    digest_paths = defaultdict(set)
    with open(CDX_HTML_FILE) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 4:
                continue
            ts, original, digest, length = parts[:4]
            path = norm_path(original)
            if not wanted(path, original):
                continue
            key = local_file(path)
            digest_paths[digest].add(key)
            caps[key].append((ts, original, digest, length))
    templates = {d for d, keys in digest_paths.items() if len(keys) >= TEMPLATE_DIGEST_MIN}
    out = {}
    for key, rows in caps.items():
        good = [r for r in rows if r[2] not in templates and r[3] != "0"]
        # prefer captures of the plain URL over ?share=... variants, then newest
        good.sort(key=lambda r: ("?" not in r[1], r[0]), reverse=True)
        seen, picks = set(), []
        for ts, original, digest, _ in good:
            if digest not in seen:
                seen.add(digest)
                picks.append((ts, original))
        out[key] = picks[:HTML_CANDIDATES]
    print(f"html: {len(templates)} template digests excluded")
    return out


def cmd_plan():
    rows = {}
    with open(CDX_FILE) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 4:
                continue
            ts, original, mime = parts[0], parts[1], parts[2]
            if not any(mime.startswith(k) for k in KEEP_TYPES):
                continue
            path = norm_path(original)
            if not wanted(path, original):
                continue
            key = local_file(path)
            rows.setdefault(key, {"path": path, "mime": mime, "caps": []})
            rows[key]["caps"].append((ts, original))
    for row in rows.values():
        # exact URL as captured, plain variants first
        row["caps"].sort(key=lambda c: ("?" in c[1], c[0]))
    html = html_captures() if os.path.exists(CDX_HTML_FILE) else {}
    no_good = 0
    for key, row in rows.items():
        if row["mime"].startswith("text/html") and key in html:
            row["caps"] = html[key]
            if not row["caps"]:
                no_good += 1
    # WordPress resized variants (foo-300x225.jpg) can be regenerated from the
    # original, so skip them whenever the full-size file is also archived.
    thumb_re = re.compile(r"-\d+x\d+(\.\w+)$")
    for key in [k for k in rows if thumb_re.search(k)]:
        if thumb_re.sub(r"\1", key) in rows:
            del rows[key]
    rows = {k: r for k, r in rows.items() if r["caps"]}
    with open(PLAN_FILE, "w") as f:
        for row in sorted(rows.values(), key=lambda r: r["path"]):
            caps = " ".join(f"{ts}|{orig}" for ts, orig in row["caps"])
            f.write(f"{row['path']}\t{row['mime']}\t{caps}\n")
    counts = defaultdict(int)
    for r in rows.values():
        counts["html" if r["mime"].startswith("text/html") else r["mime"].split("/")[0]] += 1
    print(f"plan: {len(rows)} files {dict(counts)}; {no_good} html pages had only 404/template captures")


# ---------------------------------------------------------------- fetch

_last_request = [0.0]


def _throttle():
    wait = _last_request[0] + MIN_INTERVAL - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.time()


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _record(entry, ok=True):
    if ok:
        with open(MANIFEST, "a") as f:
            f.write(json.dumps(entry) + "\n")
    else:
        with open(FAILURES, "a") as f:
            f.write(f"{entry['path']}\t{entry['original']}\t{entry.get('error', '')}\n")


def fetch_one(path, mime, caps):
    """Returns 'ok', 'fail' or 'skip'."""
    dest = local_file(path)
    if os.path.exists(dest):
        return "skip"
    is_html = mime.startswith("text/html")
    err, original = "no capture", ""
    for cap in caps.split():
        ts, original = cap.split("|", 1)
        url = f"https://web.archive.org/web/{ts}id_/{original}"
        delay = 10
        for attempt in range(5):
            _throttle()
            try:
                _, final, ctype, body = http_get(url)
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code}"
                if e.code in (404, 403, 410):
                    break  # try the next capture
                if e.code == 429:
                    _log(f"429 for {path}, pausing")
                    time.sleep(BLOCK_PAUSE)
                    continue
            except Exception as e:  # timeouts, connection resets/refusals
                err = repr(e)
                if "refused" in err.lower() or "reset" in err.lower():
                    _log(f"connection refused, pausing {BLOCK_PAUSE // 60} min ({path})")
                    time.sleep(BLOCK_PAUSE)
                    continue
            else:
                if not body or (is_html and SOFT_404.search(body[:20000])):
                    err = "empty or soft-404"
                    break
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    f.write(body)
                os.replace(tmp, dest)
                m = re.search(r"/web/(\d{14})", final)
                _record({"path": path, "original": original, "file": os.path.relpath(dest, ARCHIVE),
                         "timestamp": m.group(1) if m else ts, "mime": ctype, "bytes": len(body)})
                return "ok"
            time.sleep(delay)
            delay = min(delay * 2, 300)
    _record({"path": path, "original": original, "error": err}, ok=False)
    return "fail"


def read_plan():
    with open(PLAN_FILE) as f:
        return [line.rstrip("\n").split("\t") for line in f if line.strip()]


def cmd_fetch(only_priority=False):
    plan = read_plan()
    # Food content first (HTML, then its images); travel/trip/class material last.
    first = set()
    if os.path.exists(PRIORITY_FILE):
        first = {line.strip() for line in open(PRIORITY_FILE) if line.strip()}
    if only_priority:
        # priority paths the capture index never listed get a best-effort attempt
        listed = {r[0] for r in plan}
        plan = [r for r in plan if r[0] in first] + [
            [p, "image/unknown", f"{LATEST_TS}|{SITE_URL}{urllib.parse.quote(p)}"]
            for p in sorted(first - listed)]
    plan.sort(key=lambda r: (r[0] not in first, bool(LOW_PRIORITY_RE.search(r[0])),
                             not r[1].startswith("text/html"), r[0]))
    stats = defaultdict(int)
    start, last_report = time.time(), 0.0
    for i, row in enumerate(plan, 1):
        if SKIP_RE.search(row[0]):
            continue
        try:
            stats[fetch_one(*row)] += 1
        except OSError as e:  # e.g. a path that collides with an existing file
            _record({"path": row[0], "original": "", "error": repr(e)}, ok=False)
            stats["fail"] += 1
        if time.time() - last_report > 60:
            last_report = time.time()
            rate = stats["ok"] / max(time.time() - start, 1) * 60
            _log(f"{i}/{len(plan)} ok={stats['ok']} skip={stats['skip']} "
                 f"fail={stats['fail']} ({rate:.1f}/min)")
    _log(f"finished: {dict(stats)}")


# ---------------------------------------------------------------- discover

REF_RE = re.compile(r'''(?:src|href)\s*=\s*["']([^"'#]+)["']''', re.I)
ASSET_EXT = re.compile(r"\.(jpe?g|gif|png|pdf|webp)$", re.I)


def cmd_discover():
    """Find images/PDFs referenced by fetched HTML that aren't in the plan, and append them."""
    plan = read_plan()
    known = {local_file(r[0]) for r in plan}
    new = {}
    for dirpath, _, files in os.walk(RAW):
        for name in files:
            if not name.endswith((".html", ".htm")):
                continue
            fp = os.path.join(dirpath, name)
            site_path = "/" + os.path.relpath(fp, RAW).replace(os.sep, "/")
            base = f"http://www.{DOMAIN}{site_path}"
            text = open(fp, encoding="utf-8", errors="replace").read()
            for ref in REF_RE.findall(text):
                ref = ref.strip()
                if ref.startswith(("mailto:", "javascript:", "data:")):
                    continue
                absu = urllib.parse.urljoin(base, ref)
                path = norm_path(absu)
                if not path or not ASSET_EXT.search(path):
                    continue
                if local_file(path) in known:
                    continue
                new[local_file(path)] = (path, absu)
    with open(PLAN_FILE, "a") as f:
        for path, absu in sorted(new.values()):
            mime = "application/pdf" if path.lower().endswith(".pdf") else "image/unknown"
            f.write(f"{path}\t{mime}\t{LATEST_TS}|{absu}\n")
    print(f"discovered {len(new)} new asset references (appended to plan)")


if __name__ == "__main__":
    cmds = {"cdx": cmd_cdx, "plan": cmd_plan, "fetch": cmd_fetch, "discover": cmd_discover}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    if sys.argv[1] == "fetch":
        cmd_fetch(only_priority="--only-priority" in sys.argv)
    else:
        cmds[sys.argv[1]]()
