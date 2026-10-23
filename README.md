# Thai Food & Travel — restored

A rebuild of Kasma Loha-unchit's **thaifoodandtravel.com**, recovered from the
Internet Archive and republished as a fast, searchable static site on GitHub Pages.

The rebuilt site covers recipes, ingredient guides (with Kasma's favorite
brands), articles, and an About section with her biography and cookbooks. The
blog, market lists, travel, trip and class pages are left in the archive;
links to them go to the Wayback Machine copy.

## How it fits together

```
scraper/crawl.py     Wayback Machine -> archive/raw/      (raw mirror, not committed)
build/extract.py     archive/raw/    -> content/          (clean JSON + images, committed)
build/generate.py    content/        -> _site/            (HTML)
pagefind             _site/          -> _site/pagefind/   (search index)
```

`content/` is the source of truth for the site: one JSON file per page
(`content/recipes/pad-thai-recipe.json`, …), `content/redirects.json` for old
URLs, and `content/media/` with every image the pages use.

## Building locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./build.sh serve        # http://localhost:8000
```

Needs Node (for `npx pagefind`).

## The raw snapshot

`archive/raw/` isn't committed. The October 2026 crawl, including the blog,
travel and class pages the rebuilt site leaves out, is attached to the
[raw-snapshot-2026-10 release](https://github.com/thaifoodandtravel/archive/releases/tag/raw-snapshot-2026-10).
Extract it into `archive/` to re-run `build/extract.py` without crawling again.

## Re-crawling the archive

Only needed to refresh `archive/raw/`. The Wayback Machine blocks fast crawlers,
so the crawler makes one request every 5 seconds; a full run takes many hours.

```sh
python3 scraper/crawl.py cdx        # capture index
python3 scraper/crawl.py plan       # choose a capture per URL
python3 scraper/crawl.py fetch      # download everything (resumable)
python3 scraper/crawl.py fetch --only-priority   # only paths in archive/priority.txt
python3 scraper/crawl.py discover   # pick up images referenced but not indexed
.venv/bin/python build/extract.py   # regenerate content/
```

## Deploying

Pushing to `main` runs `.github/workflows/pages.yml`, which builds the site and
publishes it to GitHub Pages (Settings → Pages → Source: **GitHub Actions**).

Old URLs keep working: recipe, ingredient and article pages keep their original
paths (`/recipes/beefno.html`), and duplicate/legacy pages redirect.

### Custom domain

The site is served at **www.thaifoodandtravel.com** (the `CNAME` file; GitHub
redirects the bare domain to `www`). DNS is hosted on Cloudflare (free plan):
four `A` records on the apex for GitHub Pages and `www` as a CNAME to
`thaifoodandtravel.github.io`, all **DNS only** (grey cloud) so GitHub can
issue the HTTPS certificate. The domain doesn't receive email; null MX, SPF
`-all` and DMARC `p=reject` records stop it being spoofed.

The registration is with eNom through Westhost (reseller), paid through 2031.

## Credits

All recipes, writing and photographs © Kasma Loha-unchit and Michael Babcock.
