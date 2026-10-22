// Full-text search powered by Pagefind (index built into /pagefind/ at deploy time).
(function () {
  "use strict";

  var root = new URL(document.body.dataset.root || "../", location.href);
  var input = document.querySelector("#q");
  var list = document.querySelector(".results");
  var status = document.querySelector(".search-status");
  var filterBar = document.querySelector(".search-filters");
  var moreBtn = document.querySelector(".more-results");
  var PAGE = 12;
  var pagefind, results = [], shown = 0, kind = "", seq = 0;

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }

  async function load() {
    try {
      pagefind = await import(new URL("pagefind/pagefind.js", root).href);
      await pagefind.options({ baseUrl: root.pathname, excerptLength: 26 });
      await pagefind.filters(); // per-query filter counts need the filter index loaded
    } catch (e) {
      status.textContent = "Search index not found. (It's generated when the site is built — run pagefind.)";
      return;
    }
    var q = new URLSearchParams(location.search).get("q") || "";
    kind = new URLSearchParams(location.search).get("type") || "";
    input.value = q;
    run();
  }

  function renderFilters(counts) {
    var kinds = Object.keys(counts || {}).filter(function (k) { return counts[k] > 0 || k === kind; });
    if (!kinds.length) { filterBar.hidden = true; return; }
    var total = kinds.reduce(function (n, k) { return n + counts[k]; }, 0);
    var html = '<button class="chip" data-kind="" aria-pressed="' + (!kind) + '">All <small>' + (kind ? "" : total) + "</small></button>";
    var order = ["Recipe", "Ingredient", "Article", "Brand", "About"];
    kinds.sort(function (a, b) { return order.indexOf(a) - order.indexOf(b); }).forEach(function (k) {
      html += '<button class="chip" data-kind="' + esc(k) + '" aria-pressed="' + (k === kind) + '">' + esc(k) +
        " <small>" + counts[k] + "</small></button>";
    });
    filterBar.innerHTML = html;
    filterBar.hidden = false;
  }

  async function run() {
    var q = input.value.trim();
    var my = ++seq;
    var url = new URL(location.href);
    if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
    if (kind) url.searchParams.set("type", kind); else url.searchParams.delete("type");
    history.replaceState(null, "", url);
    if (!q) {
      list.innerHTML = ""; status.textContent = ""; filterBar.hidden = true; moreBtn.hidden = true;
      return;
    }
    var opts = kind ? { filters: { type: kind } } : {};
    var search = await pagefind.debouncedSearch(q, opts, 180);
    if (search === null || my !== seq) return;
    // counts per type come from an unfiltered search
    var counts = kind ? (await pagefind.search(q)).filters.type : search.filters.type;
    if (my !== seq) return;
    renderFilters(counts);
    results = search.results;
    shown = 0;
    list.innerHTML = "";
    status.textContent = results.length ? results.length + " result" + (results.length === 1 ? "" : "s") + " for “" + q + "”"
      : "No results for “" + q + "”. Try a simpler word, or an English name instead of a Thai one.";
    more();
  }

  async function more() {
    var batch = results.slice(shown, shown + PAGE);
    shown += batch.length;
    var my = seq;
    var data = await Promise.all(batch.map(function (r) { return r.data(); }));
    if (my !== seq) return;
    list.insertAdjacentHTML("beforeend", data.map(function (d) {
      var m = d.meta || {};
      var img = m.image ? '<img src="' + esc(new URL(m.image, root).href) + '" alt="" loading="lazy">' : "";
      return '<li class="result"><a href="' + esc(d.url) + '">' + img + "<div>" +
        '<div class="r-kind">' + esc(m.kind || "") + (m.date ? " · " + esc(m.date) : "") + "</div>" +
        '<div class="r-title">' + esc(m.title || d.url) + "</div>" +
        (m.thai ? '<div class="r-thai">' + esc(m.thai) + "</div>" : "") +
        '<p class="r-excerpt">' + d.excerpt + "</p></div></a></li>";
    }).join(""));
    moreBtn.hidden = shown >= results.length;
  }

  input.addEventListener("input", run);
  moreBtn.addEventListener("click", more);
  filterBar.addEventListener("click", function (e) {
    var b = e.target.closest(".chip");
    if (!b) return;
    kind = b.dataset.kind;
    run();
  });
  load();
})();
