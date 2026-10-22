#!/usr/bin/env python3
"""Extract clean, structured content from the raw Wayback mirror.

    .venv/bin/python build/extract.py

Reads archive/raw/ (see scraper/crawl.py) and writes:

    content/<type>/<slug>.json   one file per recipe / ingredient / article / post / ...
    content/redirects.json       old URL -> new URL (duplicates, renamed blog posts)
    content/media/<path>         every image the content references (thumbnails
                                 regenerated from full-size originals when needed)

Scope: recipes, ingredients (with Kasma's favorite brands) and articles, plus a
short About section. The blog, market lists, travel, trips and classes stay in
the archive; links to them point at the Wayback Machine.

Internal links and image paths in `body` are stored as absolute site paths
(e.g. /recipes/beefno.html); the site builder makes them relative.
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
import urllib.parse as up
from collections import defaultdict

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "archive", "raw")
MANIFEST = os.path.join(ROOT, "archive", "manifest.jsonl")
OUT = os.path.join(ROOT, "content")
MEDIA = os.path.join(OUT, "media")

DOMAIN = "thaifoodandtravel.com"
SITE = f"http://www.{DOMAIN}"
WAYBACK = "https://web.archive.org/web/2016/"

# Blog posts are included when they carry one of these categories.
FOOD_CATEGORIES = {"Food", "Cooking", "ingredient", "Markets", "Restaurant"}

# Top-level pages worth keeping, by type. Anything else at the root is skipped.
ROOT_PAGES = {
    "article": ["balance", "fishsauce", "cpaste", "jasrice", "coconut", "coconuti",
                "recipen", "healthlinks"],
    "about": ["about", "bio", "biointer", "books", "irf", "irfrev", "ds", "dsrevs",
              "dsdamon", "bkkpost"],
    "brand": ["brandsp"],
}
SECTION_TYPES = {
    "recipes": "recipe",
    "ingredients": "ingredient",
    "brands": "brand",
    "features": "article",
}
# Index pages: used for recipe categories / ordering, then turned into the new indexes.
INDEX_PAGES = {"/recipe.html", "/ingredients.html", "/features.html", "/brands.html",
               "/food.html", "/more.html", "/index.html", "/herb.html"}
# Paths whose content stays in the archive (linked via the Wayback Machine).
ARCHIVE_ONLY_RE = re.compile(
    r"^/(travel|classes|trips?|tours|cgi-bin|support|wordpress)\b|^/[^/]*(trip|tour|class|cooking\d*|intensive)[^/]*\.html$"
    r"|^/features/(ruenmai|mychoice|hortusb)/", re.I)

# Printable duplicates and hand-made "recipes with X" lists (ingredient pages
# generate those automatically).
SKIP_PAGE_RE = re.compile(r"/printable[/-]|/rec(cilan|coc|pkn)\.html$")
PRINTABLE_RE = re.compile(r"^(.*/)printable[/-](.*?)p?\.html$")

TYPE_DIRS = {"recipe": "recipes", "ingredient": "ingredients", "brand": "brands",
             "article": "articles", "market": "markets", "about": "about", "post": "blog"}

BOILERPLATE_RE = re.compile(
    r"^\(?\s*click (on )?(an |the )?(image|picture|photo)s?\b.*(larger|enlarge|bigger)", re.I)


# ------------------------------------------------------------------ helpers

def log(*a):
    print(*a, file=sys.stderr)


def text(el):
    return " ".join(el.get_text(" ").split()) if el else ""


def load_soup(path):
    raw = open(path, "rb").read()
    return BeautifulSoup(raw, "lxml")


def site_path_of(file_path):
    rel = "/" + os.path.relpath(file_path, RAW).replace(os.sep, "/")
    if rel.endswith("/index.html") and rel != "/index.html":
        rel = rel[: -len("index.html")]
    return rel


def resolve(href, page_path):
    """Resolve an href found on page_path to (kind, value).

    kind: 'site' (absolute site path, fragment kept), 'external' (URL), or None.
    """
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "data:")):
        return ("mailto", href) if href.startswith("mailto:") else (None, href)
    if href.startswith("#"):
        return ("anchor", href)
    absu = up.urljoin(SITE + page_path, href)
    u = up.urlsplit(absu)
    host = (u.hostname or "").lower()
    if host.endswith("web.archive.org"):
        m = re.match(r"/web/\d+[a-z_]*/(.*)", u.path)
        if m:
            return resolve(m.group(1), page_path)
    if not host.endswith(DOMAIN):
        return ("external", absu)
    path = up.unquote(u.path) or "/"
    path = re.sub(r"/{2,}", "/", path)
    return ("site", path + (("#" + u.fragment) if u.fragment else ""))


def raw_file_for(path):
    p = path.split("#")[0]
    last = p.rsplit("/", 1)[-1]
    if p.endswith("/") or "." not in last:
        p = p.rstrip("/") + "/index.html"
    return os.path.join(RAW, p.lstrip("/"))


DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")


def parse_date(s):
    m = DATE_RE.search(s or "")
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def slugify(s):
    s = re.sub(r"[^\w\s-]", "", s.lower())
    return re.sub(r"[\s_-]+", "-", s).strip("-")[:80]


def split_title(title):
    """'Galanga – Kah' -> ('Galanga', 'Kah'); Thai names follow an en/em dash."""
    title = re.sub(r"\s*\*\s*$", "", title).strip()
    m = re.match(r"^(.*?)\s+[–—]\s+(.+)$", title)
    if m and len(m.group(2)) < 60:
        return m.group(1).strip(), m.group(2).strip()
    return title, None


# ------------------------------------------------------------------ inventory

def load_capture_dates():
    dates = {}
    if os.path.exists(MANIFEST):
        for line in open(MANIFEST):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "file" in d:
                dates[os.path.join(ROOT, "archive", d["file"])] = d["timestamp"]
    return dates


def is_photo_page(soup):
    """Legacy one-photo pages: a big image, a caption, little else."""
    c = soup.select_one("#content")
    body = c or soup.body
    if body is None:
        return False
    imgs = body.find_all("img")
    words = len(text(body).split())
    title = text(soup.title)
    if c is None and soup.select_one("table") and words < 140 and imgs:
        return True
    if title.endswith(("Photo", "Photograph")) and words < 200:
        return True
    if re.search(r"\d\d[a-z]?\.html$", str(getattr(soup, "_path", ""))) and words < 250 and imgs:
        return True
    return bool(c is not None and words < 90 and imgs)


def meta_refresh(soup):
    m = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if not m:
        return None
    mm = re.search(r"url\s*=\s*['\"]?([^'\"\s>]+)", m.get("content", ""), re.I | re.S)
    return mm.group(1) if mm else None


def classify(path):
    """Return the content type for a site path, or None to skip it."""
    if path in INDEX_PAGES or ARCHIVE_ONLY_RE.search(path) or SKIP_PAGE_RE.search(path):
        return None
    parts = path.strip("/").split("/")
    if len(parts) == 1:
        name = parts[0].rsplit(".", 1)[0]
        for typ, names in ROOT_PAGES.items():
            if name in names:
                return typ
        return None
    return SECTION_TYPES.get(parts[0])


# ------------------------------------------------------------------ body cleaning

KEEP_TAGS = {"p", "h2", "h3", "h4", "h5", "ul", "ol", "li", "a", "strong", "b", "em", "i",
             "blockquote", "br", "img", "figure", "figcaption", "table", "thead", "tbody",
             "tr", "td", "th", "sup", "sub", "dl", "dt", "dd", "hr", "iframe", "cite", "small"}
DROP_TAGS = {"script", "style", "form", "input", "select", "button", "noscript", "object",
             "embed", "map", "area", "link", "meta", "head", "title"}
KEEP_ATTRS = {"a": {"href", "id", "name"}, "img": {"src", "alt", "width", "height"},
              "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"},
              "iframe": {"src", "width", "height", "allowfullscreen"},
              "figure": {"class"}, "h2": {"id"}, "h3": {"id"}, "p": {"id"}, "ul": {"class"}}
IMG_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp)$", re.I)


class Extractor:
    def __init__(self):
        self.capture_dates = load_capture_dates()
        self.items = {}          # url -> item
        self.aliases = {}        # old site path -> canonical site path (pre-rewrite)
        self.media = set()       # site paths of images referenced

    # ---- images

    def figure(self, soup, img, full, caption, side=None):
        fig = soup.new_tag("figure")
        fig["class"] = ["photo"] + ([side] if side else [])
        new_img = soup.new_tag("img", src=img["src"], alt=img.get("alt", caption or ""))
        for k in ("width", "height"):
            if img.get(k, "").isdigit():
                new_img[k] = img[k]
        if full and full != img["src"]:
            a = soup.new_tag("a", href=full)
            a["class"] = "zoom"
            a.append(new_img)
            fig.append(a)
        else:
            fig.append(new_img)
        if caption:
            fc = soup.new_tag("figcaption")
            fc.string = caption
            fig.append(fc)
        return fig

    def convert_images(self, soup, root, page_path):
        """Normalize every image into <figure class="photo"> with an optional zoom link."""
        # WordPress captioned images
        for cap in root.select("div.wp-caption, dl.wp-caption"):
            img = cap.find("img")
            if not img:
                cap.decompose()
                continue
            a = img.find_parent("a")
            cap_text = text(cap.select_one(".wp-caption-text, dd"))
            side = "left" if "alignleft" in cap.get("class", []) else "right" if "alignright" in cap.get("class", []) else None
            fixed = self.fix_img(img, page_path)
            if fixed is None:
                cap.decompose()
                continue
            cap.replace_with(self.figure(soup, fixed, self.full_src(a, page_path), cap_text, side))
        for img in list(root.find_all("img")):
            if img.find_parent("figure"):
                continue
            if not img.get("src"):
                img.decompose()
                continue
            cls = " ".join(img.get("class", []) + [img.get("align", "")]).lower()
            side = "left" if "left" in cls else "right" if "right" in cls else None
            a = img.find_parent("a")
            caption = ""
            if a is not None:
                caption = a.get("title", "") or ""
            caption = BeautifulSoup(caption, "lxml").get_text(" ").strip() if "<" in caption else caption
            fixed = self.fix_img(img, page_path)
            if fixed is None:
                (a or img).decompose()
                continue
            fig = self.figure(soup, fixed, self.full_src(a, page_path), caption, side)
            target = a if a is not None and len(a.find_all("img")) == 1 and not text(a) else img
            # a figure can't live inside a <p> or heading; hoist it before that block
            parent_p = target.find_parent(["p", "h2", "h3", "h4", "h5", "strong", "em"])
            if parent_p is not None and parent_p.find_parent() is not None:
                target.extract()
                parent_p.insert_before(fig)
            else:
                target.replace_with(fig)

    def fix_img(self, img, page_path):
        kind, val = resolve(img.get("src"), page_path)
        if kind != "site":
            return None  # remote images (counters, badges) are dropped
        src = val.split("#")[0]
        if not IMG_EXT_RE.search(src):
            return None
        if re.search(r"/(spacer|blank|dot|clear|pixel)\.gif$|/support/|/smilies/", src, re.I):
            return None
        img["src"] = src
        self.media.add(src)
        return img

    def full_src(self, a, page_path):
        """If an image is wrapped in a link to a bigger image (directly or via a
        legacy photo page), return that image's site path."""
        if a is None or not a.get("href"):
            return None
        kind, val = resolve(a["href"], page_path)
        if kind != "site":
            return None
        val = val.split("#")[0]
        if IMG_EXT_RE.search(val):
            self.media.add(val)
            return val
        f = raw_file_for(val)
        if val.endswith(".html") and os.path.exists(f):
            ps = load_soup(f)
            if is_photo_page(ps):
                big = max(ps.find_all("img"), default=None,
                          key=lambda i: int(i.get("width", "0") or 0) if str(i.get("width", "0")).isdigit() else 0)
                if big is not None:
                    k2, v2 = resolve(big.get("src"), val)
                    if k2 == "site" and IMG_EXT_RE.search(v2):
                        self.media.add(v2)
                        return v2
        return None

    # ---- general cleanup

    def clean(self, soup, root, page_path):
        for c in root.find_all(string=lambda s: isinstance(s, Comment)):
            c.extract()
        for t in root.find_all(DROP_TAGS):
            t.decompose()
        for sel in [".returntotop", "[class^=dividing-line]", ".navigation", ".postmetadata",
                    "#comments", ".commentlist", "#respond", ".sharedaddy", ".wpcnt",
                    "#jp-post-flair", ".sd-sharing-enabled", ".jp-relatedposts", "table[summary*=navigation]"]:
            for t in root.select(sel):
                t.decompose()
        self.convert_images(soup, root, page_path)
        for t in root.find_all(True):
            if t.name not in KEEP_TAGS:
                t.unwrap()
                continue
            allowed = KEEP_ATTRS.get(t.name, set())
            for k in list(t.attrs):
                if k not in allowed:
                    del t[k]
            if t.name == "ul" and t.get("class") and "recipe" not in t["class"]:
                del t["class"]
            if t.name == "b":
                t.name = "strong"
            if t.name == "i":
                t.name = "em"
            if t.name == "iframe":
                kind, val = resolve(t.get("src"), page_path)
                if kind != "external" or not re.search(r"youtube|vimeo", val):
                    t.decompose()
                    continue
                t["src"] = val.replace("http://", "https://")
        for a in root.find_all("a"):
            if a.get("class") == ["zoom"] or "zoom" in (a.get("class") or []):
                continue
            self.mark_link(a, page_path)
        # strip boilerplate paragraphs and empties
        for p in root.find_all(["p", "li", "h2", "h3", "h4", "strong", "em"]):
            t = text(p)
            if BOILERPLATE_RE.search(t) or t in ("Return to top", "back to top"):
                p.decompose()
            elif not t and not p.find(["img", "figure", "iframe", "br"]):
                p.decompose()
        # collapse runs of <br>
        for br in root.find_all("br"):
            nxt = br.next_sibling
            while isinstance(nxt, NavigableString) and not nxt.strip():
                nxt = nxt.next_sibling
            if isinstance(nxt, Tag) and nxt.name == "br":
                br.decompose()
        return root

    def mark_link(self, a, page_path):
        """Store links in a normalized form; final targets are resolved in rewrite()."""
        kind, val = resolve(a.get("href"), page_path)
        if kind in (None,):
            a.unwrap()
        elif kind == "site":
            a["href"] = "site:" + val
        elif kind == "external":
            a["href"] = val
        elif kind == "mailto":
            a.unwrap()  # contact addresses are retired

    def body_html(self, root):
        html = "".join(str(c) for c in root.contents)
        html = re.sub(r"(\s*<br/?>\s*)+(</p>)", r"\2", html)
        html = re.sub(r"\n\s*\n+", "\n", html)
        return html.strip()

    # ---- per-type extraction

    def base_item(self, typ, path, soup, title, file_path):
        desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        ts = self.capture_dates.get(file_path, "")
        return {
            "type": typ,
            "url": path,
            "title": title,
            "description": " ".join(desc.get("content", "").split()) if desc else "",
            "source": SITE + path,
            "archived": ts,
            "wayback": f"https://web.archive.org/web/{ts}/{SITE}{path}" if ts else "",
        }

    def extract_static(self, typ, path, file_path, soup):
        content = soup.select_one("#content")
        if content is None:
            content = self.largest_block(soup)
        if content is None:
            return None
        h1 = content.find("h1") or soup.find("h1")
        title = text(h1) or text(soup.title)
        thai_from_h1 = None
        if h1 is not None and h1.find("br"):
            # "English Name <br/> <em>Thai Name</em> (or <em>Alt</em>)*"
            html_ = h1.decode_contents()
            before, _, after = html_.partition(h1.find("br").decode())
            before_t = text(BeautifulSoup(before, "lxml"))
            after_t = re.sub(r"\s*\*\s*$", "", text(BeautifulSoup(after, "lxml")))
            if re.match(r"^Adventures in Thai Cooking", before_t):
                title = after_t
            elif before_t and after_t:
                title, thai_from_h1 = before_t.rstrip(" –—-"), after_t
        title = re.sub(r"\s*\*\s*$", "", title)
        title = re.sub(r"^Adventures in Thai Cooking (&|and) Travel\s*", "", title).strip() or text(soup.title)
        if h1:
            h1.decompose()
        item = self.base_item(typ, path, soup, title, file_path)
        attrib = content.select_one("p.attrib")
        copyright_ = " ".join(text(e) for e in content.select(".centerbi"))
        for e in content.select("p.attrib, .centerbi"):
            e.decompose()
        if attrib is not None:
            item["byline"] = text(attrib)
        m = re.search(r"Copyright\s*©?\s*(?:&copy;)?\s*([\d\s,&-]+)", copyright_ + " " + item.get("byline", ""))
        if m:
            years = re.findall(r"\d{4}", m.group(1))
            if years:
                item["year"] = int(years[0])
        if copyright_:
            item["copyright"] = copyright_
        item["author"] = "Michael Babcock" if "Babcock" in item.get("byline", "") else "Kasma Loha-unchit"
        name, thai = split_title(title)
        thai = thai_from_h1 or thai
        if typ in ("recipe", "ingredient"):
            item["title"] = re.sub(r"\s+Recipe$", "", name if thai and not thai_from_h1 else title)
            if thai and thai != item["title"]:
                thai = thai.strip()
                thai += ")" * (thai.count("(") - thai.count(")"))
                thai = re.sub(r"\s+\)", ")", re.sub(r"\(\s+", "(", thai))
                item["thai_name"] = thai
        if typ == "recipe":
            lists = content.select("ul.recipe")
            ingredients = [text(li) for ul in lists for li in ul.find_all("li")]
            if ingredients:
                item["ingredients"] = ingredients
            serves = re.search(r"\b(Serves [^.]{1,80}\.)", text(content))
            if serves:
                item["serves"] = serves.group(1)
        self.clean(soup, content, path)
        item["body"] = self.body_html(content)
        return item

    def largest_block(self, soup):
        """For pre-2005 table layouts: the cell with the most paragraph text."""
        best, best_len = None, 0
        for cell in soup.find_all(["td", "div"]):
            n = sum(len(text(p)) for p in cell.find_all("p", recursive=False)) or len(text(cell)) * 0.3
            if n > best_len:
                best, best_len = cell, n
        return best

    def extract_post(self, path, file_path, soup, static_soup=None):
        """WordPress post page (preferred: has tags, categories, comments)."""
        post = soup.select_one("div.post, div.widecolumn, article")
        entry = soup.select_one(".entry")
        if entry is None:
            return None
        h = (post.find(["h1", "h2"]) if post else None) or soup.find("h2")
        title = text(h) or text(soup.title).split("«")[0].strip()
        title = re.sub(r"\s*(?:&#8211;|–)\s*Thai Food and Travel Blog\s*$", "", title)
        item = self.base_item("post", path, soup, title, file_path)
        item["categories"] = sorted({text(a) for a in soup.select("a[rel~=category]")})
        item["tags"] = sorted({text(a).lower() for a in soup.select("a[rel~=tag]")} - {c.lower() for c in item["categories"]})
        meta = text(soup.select_one(".postmetadata")) + " " + " ".join(text(s) for s in soup.select("small"))
        item["date"] = parse_date(meta)
        item["author"] = "Michael Babcock" if re.search(r"Michael Babcock|\bMichael\b,", meta) else "Kasma Loha-unchit"
        item["comments"] = self.comments(soup)
        self.clean(soup, entry, path)
        item["body"] = self.body_html(entry)
        return item

    def extract_static_post(self, path, file_path, soup):
        entry = soup.select_one(".entry")
        if entry is None:
            return None
        h = soup.select_one(".widecolumn h2, .widecolumn h1") or soup.find("h2")
        title = text(h) or text(soup.title)
        item = self.base_item("post", path, soup, title, file_path)
        meta = " ".join(text(s) for s in soup.select("small"))
        item["date"] = parse_date(meta)
        item["author"] = "Michael Babcock" if "Michael" in meta else "Kasma Loha-unchit"
        item["categories"], item["tags"], item["comments"] = [], [], []
        self.clean(soup, entry, path)
        item["body"] = self.body_html(entry)
        return item

    def comments(self, soup):
        out = []
        ol = soup.select_one("ol.commentlist, ul.commentlist")
        if ol is None:
            return out

        def walk(lst, depth, parent):
            for li in lst.find_all("li", recursive=False):
                body = li.select_one(".comment-body") or li
                author = text(body.select_one("cite, .fn, .comment-author"))
                author = re.sub(r"\s*says:?\s*$", "", author)
                when = parse_date(text(body.select_one(".commentmetadata, .comment-meta")))
                paras = [p for p in body.find_all("p", recursive=True)
                         if not p.find_parent(["ul", "ol"], class_="children")
                         and not p.find_parent(class_=re.compile("reply|comment-meta"))]
                html = "".join(f"<p>{p.decode_contents().strip()}</p>" for p in paras if text(p))
                html = re.sub(r"<(?!/?(p|br|em|strong|i|b)\b)[^>]+>", "", html)
                cid = li.get("id", "")
                if html:
                    out.append({"id": cid, "author": author or "Anonymous", "date": when,
                                "depth": depth, "parent": parent, "body": html,
                                "kasma": bool(re.search(r"kasma", author, re.I))})
                children = li.find(["ul", "ol"], class_="children")
                if children is not None:
                    walk(children, depth + 1, cid)

        walk(ol, 0, None)
        return out

    # ---- recipe categories from the old recipe index

    def recipe_categories(self):
        cats = defaultdict(list)
        f = os.path.join(RAW, "recipe.html")
        if not os.path.exists(f):
            return cats
        soup = load_soup(f)
        content = soup.select_one("#content")
        cur = None
        order = 0
        for el in content.descendants:
            if getattr(el, "name", None) == "h2":
                cur = text(el).replace("Thai ", "", 1)
            elif getattr(el, "name", None) == "a" and cur and el.get("href"):
                kind, val = resolve(el["href"], "/recipe.html")
                if kind == "site" and not val.startswith("#"):
                    order += 1
                    cats[val.split("#")[0]].append(cur)
                    if "#" in val:
                        cats["__embedded__"].append((val, cur, text(el)))
        return cats

    # ---- driver

    def run(self):
        files = []
        for dp, _, fs in os.walk(RAW):
            for f in fs:
                if f.endswith((".html", ".htm")):
                    files.append(os.path.join(dp, f))
        files.sort()

        wp_posts, static_posts = {}, {}
        for fp in files:
            path = site_path_of(fp)
            m = PRINTABLE_RE.match(path)
            if m:
                self.aliases[path] = f"{m.group(1)}{m.group(2)}.html"
            typ = classify(path)
            if typ is None:
                continue
            soup = load_soup(fp)
            soup._path = path
            target = meta_refresh(soup)
            if target:
                kind, val = resolve(target, path)
                if kind == "site":
                    self.aliases[path] = val
                continue
            if typ == "post":
                m = re.match(r"^/blog/posts/([^/]+)\.html$", path)
                if m:
                    static_posts[m.group(1)] = (fp, soup)
                    continue
                m = re.match(r"^/blog/([^/]+)/$", path)
                if m and m.group(1) not in ("about", "blog-index", "wednesday-photo-index"):
                    wp_posts[m.group(1)] = (fp, soup)
                continue
            # brand pages are short by nature (a bottle photo and a note)
            if typ != "brand" and is_photo_page(soup):
                continue
            item = self.extract_static(typ, path, fp, soup)
            if item and item["body"]:
                self.items[path] = item

        # blog: WordPress version preferred, 2022 static export as fallback
        for slug in sorted(set(wp_posts) | set(static_posts)):
            url = f"/blog/{slug}/"
            item = None
            if slug in wp_posts:
                fp, soup = wp_posts[slug]
                item = self.extract_post(url, fp, soup)
            if (item is None or not item["body"]) and slug in static_posts:
                fp, soup = static_posts[slug]
                item = self.extract_static_post(url, fp, soup)
            if item is None or not item["body"]:
                continue
            item["slug"] = slug
            self.aliases[f"/blog/posts/{slug}.html"] = url
            self.aliases[f"/blog/{slug}"] = url
            if item["categories"] and not FOOD_CATEGORIES & set(item["categories"]):
                self.excluded_posts.add(url)
                continue
            self.items[url] = item

        self.dedupe()
        cats = self.recipe_categories()
        for path, item in self.items.items():
            if item["type"] == "recipe":
                item["categories"] = cats.get(path, [])
        for val, cat, label in cats.get("__embedded__", []):
            target = self.canonical(val.split("#")[0])
            if target in self.items:
                self.items[target].setdefault("embedded_recipes", []).append(
                    {"title": label, "anchor": val.split("#", 1)[1], "category": cat})
        self.rewrite()
        self.write()

    excluded_posts = set()

    def dedupe(self):
        """Same title in the same type -> keep the richer page, alias the other."""
        by_key = defaultdict(list)
        for path, item in self.items.items():
            key = (item["type"], slugify(item["title"]))
            by_key[key].append(path)
        for key, paths in by_key.items():
            if len(paths) < 2:
                continue
            ranked = sorted(paths, key=lambda p: (self.items[p].get("archived", ""),
                                                  len(self.items[p]["body"])), reverse=True)
            # prefer descriptive filenames ("basil-chicken-recipe") over legacy short ones
            ranked.sort(key=lambda p: "-" not in p.rsplit("/", 1)[-1])
            keep = ranked[0]
            for p in ranked[1:]:
                self.aliases[p] = keep
                del self.items[p]

    def canonical(self, path):
        seen = set()
        while path in self.aliases and path not in seen:
            seen.add(path)
            path = self.aliases[path]
        return path

    def rewrite(self):
        """Resolve 'site:' links: to an item, to an image, or to the Wayback Machine."""
        for item in self.items.values():
            soup = BeautifulSoup(item["body"], "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not href.startswith("site:"):
                    continue
                path = href[5:]
                base, _, frag = path.partition("#")
                target = self.canonical(base)
                if target == item["url"] and frag:
                    a["href"] = "#" + frag
                elif target in self.items:
                    a["href"] = target + ("#" + frag if frag else "")
                elif IMG_EXT_RE.search(base) or base.lower().endswith(".pdf"):
                    self.media.add(base)
                    a["href"] = base
                elif base in ("/", "/index.html"):
                    a["href"] = "/"
                elif base in ("/recipe.html", "/recipes/"):
                    a["href"] = "/recipes/"
                elif base in ("/ingredients.html", "/herb.html"):
                    a["href"] = "/ingredients/"
                elif base in ("/features.html",):
                    a["href"] = "/articles/"
                else:
                    # not part of the rebuilt site: send readers to the archived copy
                    a["href"] = WAYBACK + SITE + path
                    a["class"] = "archived"
            body = soup.body
            item["body"] = "".join(str(c) for c in body.contents) if body else ""

    def write(self):
        if os.path.isdir(OUT):
            for d in TYPE_DIRS.values():
                shutil.rmtree(os.path.join(OUT, d), ignore_errors=True)
        counts = defaultdict(int)
        used = defaultdict(set)
        for path, item in sorted(self.items.items()):
            d = TYPE_DIRS[item["type"]]
            slug = item.get("slug") or slugify(path.strip("/").rsplit(".", 1)[0].split("/", 1)[-1].replace("/", "-"))
            while slug in used[d]:
                slug += "-2"
            used[d].add(slug)
            item["slug"] = slug
            os.makedirs(os.path.join(OUT, d), exist_ok=True)
            with open(os.path.join(OUT, d, slug + ".json"), "w") as f:
                json.dump(item, f, indent=1, ensure_ascii=False)
            counts[item["type"]] += 1
        redirects = {old: self.canonical(old) for old in self.aliases
                     if self.canonical(old) in self.items and old != self.canonical(old)}
        with open(os.path.join(OUT, "redirects.json"), "w") as f:
            json.dump(dict(sorted(redirects.items())), f, indent=1)
        copied, missing, resized = copy_media(self.media)
        log(f"items: {dict(counts)}  (blog posts excluded as non-food: {len(self.excluded_posts)})")
        log(f"redirects: {len(redirects)}")
        log(f"media: {copied} copied, {resized} thumbnails generated, {len(missing)} missing")
        with open(os.path.join(OUT, "missing-media.txt"), "w") as f:
            f.write("\n".join(sorted(missing)) + "\n")


THUMB_RE = re.compile(r"^(.*)-(\d+)x(\d+)(\.\w+)$")


def copy_media(paths):
    """Copy referenced images into content/media; regenerate WP thumbnails we skipped."""
    from PIL import Image

    copied = resized = 0
    missing = []
    # drop media nothing references any more
    keep = {p.lstrip("/") for p in paths}
    for dp, _, fs in os.walk(MEDIA):
        for f in fs:
            rel = os.path.relpath(os.path.join(dp, f), MEDIA).replace(os.sep, "/")
            if rel not in keep:
                os.remove(os.path.join(dp, f))
    for p in sorted(paths):
        src = os.path.join(RAW, p.lstrip("/"))
        dst = os.path.join(MEDIA, p.lstrip("/"))
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            copied += 1
            continue
        if os.path.exists(src) and os.path.getsize(src) > 0:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            copied += 1
            continue
        m = THUMB_RE.match(p)
        orig = os.path.join(RAW, (m.group(1) + m.group(4)).lstrip("/")) if m else None
        if orig and os.path.exists(orig):
            try:
                with Image.open(orig) as im:
                    w, h = int(m.group(2)), int(m.group(3))
                    im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
                    im.thumbnail((w, h))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    im.save(dst, quality=85)
                resized += 1
                continue
            except OSError:
                pass
        missing.append(p)
    return copied, missing, resized


if __name__ == "__main__":
    Extractor().run()
