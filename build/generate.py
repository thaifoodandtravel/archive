#!/usr/bin/env python3
"""Build the static site from content/ into _site/.

    .venv/bin/python build/generate.py            # HTML + media
    npx -y pagefind@1.3.0 --site _site        # search index (run after)

All internal links are written relative to the page, so the output works at any
base path (a github.io project URL, a sub-path of another domain, or a custom domain).
"""
import datetime as dt
import glob
import html
import json
import os
import posixpath
import re
import shutil
import sys
from collections import defaultdict

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(ROOT, "content")
MEDIA = os.path.join(CONTENT, "media")
OUT = os.path.join(ROOT, "_site")
HERE = os.path.dirname(os.path.abspath(__file__))

# Where the site is served from: "/" for a custom domain, "/<repo>/" for a
# github.io project site. Only the 404 page needs it (everything else is relative).
BASE_PATH = os.environ.get("BASE_PATH", "/")

SITE_NAME = "Thai Food & Travel"
SITE_TAGLINE = "Recipes, ingredients and stories from Kasma Loha-unchit"

TYPE_LABELS = {"recipe": "Recipe", "ingredient": "Ingredient", "brand": "Brand",
               "article": "Article", "about": "About"}

RECIPE_COURSES = ["Appetizers", "Soups", "Salads", "Curries", "Main Courses", "Vegetable Dishes",
                  "Noodles & Rice Dishes", "Rice", "Sauces", "Desserts", "Beverages"]

# Search terms for each ingredient page (lower case). Matched against recipe
# ingredient lists (and article titles) to build "recipes using ..." lists.
INGREDIENT_TERMS = {
    "basil": ["thai basil", "horapa"],
    "basilh": ["holy basil", "gkaprow", "kaprao", "krapow"],
    "bstickyr": ["black sticky rice", "black glutinous rice"],
    "cilantro": ["cilantro", "coriander", "pak chee"],
    "cocmilk": ["coconut milk", "coconut cream", "gkati"],
    "coconut": ["coconut"],
    "cumin": ["cumin"],
    "fishsauce": ["fish sauce", "nahm bplah", "nam pla"],
    "galanga": ["galanga", "galangal", "kah"],
    "garlic": ["garlic"],
    "gkabi": ["shrimp paste", "gkabi", "gkabpi", "kapi"],
    "gkrachai": ["krachai", "gkrachai", "lesser ginger", "fingerroot"],
    "greenp": ["green papaya", "unripe papaya"],
    "klime": ["kaffir lime", "makrut", "magrood"],
    "lemongrass": ["lemongrass", "lemon grass", "dtakrai"],
    "limes": ["lime juice", "limes", "manao"],
    "limestone": ["limestone"],
    "oysters": ["oyster sauce"],
    "palmsug": ["palm sugar", "coconut sugar"],
    "pandan": ["pandan", "pandanus", "bai dteuy"],
    "peppercorns": ["peppercorn", "white pepper", "black pepper"],
    "pkn": ["thai chilli", "thai chile", "prik kee noo", "bird's eye", "chillies", "chilies"],
    "rflour": ["rice flour"],
    "roastedc": ["roasted chilli paste", "nahm prik pao", "nam prik pao"],
    "rrice": ["roasted rice", "kao kua", "khao khua"],
    "saltedv": ["salted radish", "tianjin", "preserved vegetable", "preserved radish"],
    "sataw": ["sataw", "sadtaw", "sator"],
    "shallot": ["shallot"],
    "soyblack": ["black soy", "dark soy"],
    "sriracha": ["sriracha"],
    "stickyr": ["sticky rice", "glutinous rice", "kao niow", "khao niow"],
    "tamarind": ["tamarind"],
    "turmeric": ["turmeric", "kamin"],
    "wbean": ["winged bean"],
}

# Kasma romanizes Thai with gk/dt/bp ("Gkaprow", "Dtom Yum"); readers usually
# search for the common spellings. Only words with those digraphs are Thai, so
# only those get variants (indexed as hidden text on each page).
IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|gif|webp)$", re.I)
THAI_WORD_RE = re.compile(r"\b[a-z]*(?:gk|dt|bp)[a-z]*\b", re.I)
ROMANIZATION = [("gk", "k"), ("dt", "t"), ("bp", "p"), ("ow", "ao"), ("ahm", "am"), ("ahn", "an")]

SKIP_SLUGS = {("brand", "palmsug")}

# Home page photo mosaic: (photo, recipe it links to), first available wins.
# The originals are at most 600px wide, so they're shown as tiles, never full-bleed.
HERO_PHOTOS = [
    ("/images/green-papaya-01.jpg", "/recipes/green-papaya-salad.html"),
    ("/images/c1-boat-noodles.jpg", "/recipes/beefno.html"),
    ("/images/baschi1.jpg", "/recipes/basil-chicken-recipe.html"),
    ("/images/asparagus-01l.jpg", "/recipes/asparagus.html"),
    ("/images/baschi-wok.jpg", "/recipes/basil-chicken-recipe.html"),
]


# ------------------------------------------------------------------ loading

def load_items():
    items = []
    for f in sorted(glob.glob(os.path.join(CONTENT, "*", "*.json"))):
        if os.path.basename(f) == "redirects.json":
            continue
        d = json.load(open(f))
        if (d["type"], d["slug"]) in SKIP_SLUGS:
            continue
        items.append(d)
    return items


def first_image(body, min_width=250):
    """First real photo in a body (skips icons and clip art) for cards and previews."""
    from PIL import Image

    for src in re.findall(r'<img[^>]+src="([^"]+)"', body):
        path = os.path.join(MEDIA, src.lstrip("/"))
        if not os.path.exists(path):
            continue
        try:
            with Image.open(path) as im:
                if im.width >= min_width and im.height >= min_width * 0.5:
                    return src
        except OSError:
            continue
    return None


def plain(body, n=None):
    t = " ".join(BeautifulSoup(body, "lxml").get_text(" ").split())
    if n and len(t) > n:
        t = t[:n].rsplit(" ", 1)[0] + "…"
    return t


def variants(*texts):
    """Common alternate spellings of Kasma's romanized Thai words, for search."""
    words = set()
    for t in texts:
        for w in THAI_WORD_RE.findall(t or ""):
            v = w.lower()
            for a, b in ROMANIZATION:
                v = v.replace(a, b)
            words.add(v)
    return " ".join(sorted(words))


# ------------------------------------------------------------------ paths

def out_file(url):
    """Site URL -> file path relative to _site."""
    if url.endswith("/"):
        return url.lstrip("/") + "index.html"
    return url.lstrip("/")


def relurl(target, page_url):
    """Relative link from page_url to target (both absolute site URLs)."""
    if re.match(r"^[a-z]+:|^#", target):
        return target
    target, hash_, frag = target.partition("#")
    page_dir = posixpath.dirname("/" + out_file(page_url))
    if target.endswith("/"):
        rel = posixpath.relpath(target.rstrip("/") or "/", page_dir)
        rel = "./" if rel == "." else rel + "/"
    else:
        rel = posixpath.relpath(target, page_dir)
    return rel + (hash_ + frag if hash_ else "")


def rewrite_body(body, page_url, missing):
    """Make every absolute site path in a body relative to page_url; drop missing images."""
    soup = BeautifulSoup(body, "lxml")
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("/"):
            if not os.path.exists(os.path.join(MEDIA, src.lstrip("/"))):
                missing.add(src)
                fig = img.find_parent("figure")
                (fig or img).decompose()
                continue
            img["src"] = relurl(src, page_url)
        img["loading"] = "lazy"
        img["decoding"] = "async"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            if IMAGE_EXT_RE.search(href) and a.find("img"):
                a["class"] = (a.get("class") or []) + ["zoom"]
            if "zoom" in (a.get("class") or []):
                if not os.path.exists(os.path.join(MEDIA, href.lstrip("/"))):
                    a.unwrap()
                    continue
            a["href"] = relurl(href, page_url)
        elif href.startswith("http") and "archived" not in (a.get("class") or []):
            a["rel"] = "noopener"
    for fig in soup.find_all("figure"):
        if not fig.find("img"):
            fig.decompose()
    for img in soup.select("figure.photo img"):
        if not img.find_parent("a"):
            link = soup.new_tag("a", href=img["src"], attrs={"class": "zoom"})
            img.wrap(link)
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(strip=True).lower() == "ingredients" and not soup.find(id="ingredients"):
            h["id"] = "ingredients"
    b = soup.body
    return "".join(str(c) for c in b.contents) if b else ""


# ------------------------------------------------------------------ build

class Site:
    def __init__(self):
        self.env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                               autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
        self.env.filters["date"] = lambda d: dt.date.fromisoformat(d).strftime("%B %-d, %Y") if d else ""
        self.items = load_items()
        self.by_url = {i["url"]: i for i in self.items}
        self.missing = set()
        self.pages = 0
        for i in self.items:
            i["image"] = first_image(i.get("body", ""))
            i["summary"] = i.get("description") or plain(i.get("body", ""), 180)
            i["label"] = TYPE_LABELS[i["type"]]
        self.link_ingredients()

    def of(self, typ):
        return [i for i in self.items if i["type"] == typ]

    def link_ingredients(self):
        ingredients = {i["slug"]: i for i in self.of("ingredient")}
        for slug, terms in INGREDIENT_TERMS.items():
            ing = ingredients.get(slug)
            if not ing:
                continue
            pats = [re.compile(r"\b" + re.escape(t) + r"s?\b", re.I) for t in terms]
            ing["recipes"], ing["articles"] = [], []
            for r in self.of("recipe"):
                hay = " ".join(r.get("ingredients") or []) or plain(r["body"])
                if any(p.search(hay) for p in pats):
                    ing["recipes"].append(r)
                    r.setdefault("uses", []).append(ing)
            for a in self.of("article"):
                if any(p.search(a["title"]) for p in pats):
                    ing["articles"].append(a)

    def render(self, template, url, **ctx):
        page_url = url
        ctx.setdefault("title", SITE_NAME)
        html_ = self.env.get_template(template).render(
            site_name=SITE_NAME, tagline=SITE_TAGLINE, page_url=page_url,
            u=lambda target: relurl(target, page_url),
            year=dt.date.today().year, **ctx)
        path = os.path.join(OUT, out_file(url))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(html_)
        self.pages += 1

    def build(self):
        if os.path.isdir(OUT):
            shutil.rmtree(OUT)
        os.makedirs(OUT)
        shutil.copytree(os.path.join(HERE, "static"), os.path.join(OUT, "static"))
        if os.path.isdir(MEDIA):
            shutil.copytree(MEDIA, OUT, dirs_exist_ok=True)

        for item in self.items:
            item["html"] = rewrite_body(item.get("body", ""), item["url"], self.missing)

        for item in self.items:
            self.render_item(item)
        self.render_indexes()
        self.render_redirects()
        for name in ("CNAME", ".nojekyll"):
            src = os.path.join(ROOT, name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(OUT, name))
        open(os.path.join(OUT, ".nojekyll"), "w").close()
        print(f"built {self.pages} pages into {os.path.relpath(OUT, ROOT)}/; "
              f"{len(self.missing)} images not (yet) available", file=sys.stderr)
        if not os.environ.get("BUILD_SH"):
            print("search index not built: run ./build.sh (or npx pagefind --site _site)", file=sys.stderr)

    def render_item(self, item):
        ctx = {"item": item, "title": item["title"],
               "search_extra": variants(item["title"], item.get("thai_name", ""), plain(item.get("body", ""))[:4000]),
               }
        if item["type"] == "recipe":
            course = (item.get("categories") or [None])[0]
            ctx["siblings"] = [r for r in self.of("recipe")
                               if course and course in r.get("categories", []) and r is not item][:6]
        self.render(f"{item['type']}.html" if item["type"] in ("recipe", "ingredient") else "page.html",
                    item["url"], **ctx)

    def render_indexes(self):
        recipes = sorted(self.of("recipe"), key=lambda r: r["title"].lower())
        by_course = defaultdict(list)
        for r in recipes:
            for c in r.get("categories") or ["More Recipes"]:
                by_course[c].append(r)
        courses = [(c, by_course[c]) for c in RECIPE_COURSES + ["More Recipes"] if by_course.get(c)]
        self.render("recipes.html", "/recipes/", title="Thai Recipes", courses=courses, count=len(recipes))

        ings = sorted(self.of("ingredient"), key=lambda i: re.sub(r"^\(thai\)\s*", "", i["title"].lower()))
        self.render("cards.html", "/ingredients/", title="Thai Ingredients", items=ings,
                    intro="Kasma's guide to the herbs, spices, sauces and staples of the Thai kitchen — what they are, "
                          "how they're used, and how to choose and store them.",
                    extra_title="Kasma's Favorite Brands",
                    extra=sorted(self.of("brand"), key=lambda b: b["title"]))
        arts = sorted(self.of("article"), key=lambda a: a["title"].lower())
        self.render("cards.html", "/articles/", title="Articles", items=arts, compact=True,
                    intro="Essays on Thai cooking, culture and technique — from balancing flavors and making curry "
                          "paste by hand to woks, coconuts and fish sauce.")
        self.render("cards.html", "/about/", title="About Kasma", items=self.of("about"), compact=True,
                    intro="Kasma Loha-unchit taught Thai cooking in the San Francisco Bay Area from 1985 and led trips "
                          "to Thailand from 1986 until she retired in 2020. Her first cookbook, It Rains Fishes, won "
                          "the Julia Child Cookbook Award.")
        self.render("search.html", "/search/", title="Search")
        featured = [self.by_url[u] for u in (
            "/recipes/pad-thai-recipe.html", "/recipes/green-papaya-salad.html", "/recipes/cocsea.html",
            "/recipes/gaengped.html", "/recipes/basil-chicken-recipe.html", "/recipes/stickrice.html",
            "/recipes/hotsou.html", "/recipes/lahb.html") if u in self.by_url]
        essentials = [i for i in ings if i["slug"] in (
            "fishsauce", "galanga", "lemongrass", "klime", "basilh", "pkn", "palmsug", "gkabi", "tamarind", "cocmilk")]
        reading = [self.by_url[u] for u in (
            "/features/spirit.html", "/features/taste.html", "/balance.html", "/features/harmony.html",
            "/features/cpaste.html", "/features/wok1.html", "/features/streetf.html", "/features/learning.html")
            if u in self.by_url]
        hero, seen = [], set()
        candidates = HERO_PHOTOS + [(r["image"], r["url"]) for r in featured if r.get("image")]
        for photo, url in candidates:
            if url in seen or url not in self.by_url or not os.path.exists(os.path.join(MEDIA, photo.lstrip("/"))):
                continue
            seen.add(url)
            hero.append({"src": photo, "url": url, "title": self.by_url[url]["title"]})
        self.render("home.html", "/", title=SITE_NAME, featured=featured, courses=courses, hero=hero[:3],
                    essentials=essentials, reading=reading,
                    counts={t: len(self.of(t)) for t in TYPE_LABELS})
        self.render("404.html", "/404.html", title="Page not found", base_href=BASE_PATH)

    def render_redirects(self):
        redirects = json.load(open(os.path.join(CONTENT, "redirects.json")))
        legacy = {"/recipe.html": "/recipes/", "/ingredients.html": "/ingredients/", "/herb.html": "/ingredients/",
                  "/features.html": "/articles/", "/brands.html": "/ingredients/", "/brandsp.html": "/ingredients/",
                  "/food.html": "/", "/more.html": "/", "/index.html": "/",
                  "/recipes/index.html": "/recipes/", "/features/index.html": "/articles/"}
        redirects.update(legacy)
        n = 0
        for old, new in redirects.items():
            if new not in self.by_url and new not in legacy.values():
                continue
            f = os.path.join(OUT, out_file(old))
            if os.path.exists(f):
                continue
            os.makedirs(os.path.dirname(f), exist_ok=True)
            target = relurl(new, old)
            with open(f, "w") as fh:
                fh.write(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moved</title>'
                         f'<meta name="robots" content="noindex">'
                         f'<link rel="canonical" href="{html.escape(target)}">'
                         f'<meta http-equiv="refresh" content="0; url={html.escape(target)}"></head>'
                         f'<body><p>This page has moved to <a href="{html.escape(target)}">{html.escape(target)}</a>.</p></body></html>')
            n += 1
        print(f"{n} redirect pages", file=sys.stderr)


if __name__ == "__main__":
    Site().build()
