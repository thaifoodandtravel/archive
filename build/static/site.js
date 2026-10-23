// Thai Food & Travel — small progressive enhancements. Everything works without JS.
(function () {
  "use strict";

  // "/" focuses the search box
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || /input|textarea|select/i.test(document.activeElement.tagName)) return;
    var box = document.querySelector("#q, #q-top");
    if (box) { e.preventDefault(); box.focus(); }
  });

  // ---- lightbox for photos that link to a larger version
  var box = document.querySelector(".lightbox");
  var zooms = Array.prototype.slice.call(document.querySelectorAll("a.zoom"));
  var current = -1;
  function show(i) {
    current = (i + zooms.length) % zooms.length;
    var a = zooms[current];
    var cap = a.closest("figure") && a.closest("figure").querySelector("figcaption");
    var img = box.querySelector("img");
    img.src = a.href;
    img.alt = (a.querySelector("img") || {}).alt || "";
    box.querySelector("figcaption").textContent = cap ? cap.textContent : "";
    box.querySelector(".lightbox-prev").hidden = box.querySelector(".lightbox-next").hidden = zooms.length < 2;
    box.hidden = false;
    document.body.style.overflow = "hidden";
  }
  function hide() { box.hidden = true; document.body.style.overflow = ""; }
  if (box && zooms.length) {
    zooms.forEach(function (a, i) {
      a.addEventListener("click", function (e) {
        if (e.metaKey || e.ctrlKey || e.shiftKey) return;
        e.preventDefault();
        show(i);
      });
    });
    // text links that start the slideshow at the first photo of a gallery
    document.querySelectorAll("[data-lightbox-start]").forEach(function (link) {
      link.addEventListener("click", function (e) {
        var first = document.querySelector(link.dataset.lightboxStart + " a.zoom");
        if (!first) return;
        e.preventDefault();
        show(zooms.indexOf(first));
      });
    });
    box.addEventListener("click", function (e) {
      if (e.target.closest(".lightbox-prev")) show(current - 1);
      else if (e.target.closest(".lightbox-next")) show(current + 1);
      else if (e.target.tagName !== "IMG") hide();
    });
    document.addEventListener("keydown", function (e) {
      if (box.hidden) return;
      if (e.key === "Escape") hide();
      if (e.key === "ArrowLeft") show(current - 1);
      if (e.key === "ArrowRight") show(current + 1);
    });
  }

  // ---- tick off ingredients while cooking
  document.querySelectorAll("ul.recipe li").forEach(function (li) {
    li.addEventListener("click", function (e) {
      if (e.target.closest("a")) return;
      li.classList.toggle("done");
    });
  });

  // ---- instant filtering on index pages
  var input = document.querySelector("#recipe-filter, #list-filter");
  var items = Array.prototype.slice.call(document.querySelectorAll(".filter-item"));
  var chips = Array.prototype.slice.call(document.querySelectorAll(".chips-filter .chip[data-category]"));
  var category = "";
  function apply() {
    var words = (input ? input.value : "").toLowerCase().split(/\s+/).filter(Boolean);
    var shown = 0;
    items.forEach(function (el) {
      var ok = words.every(function (w) { return el.dataset.text.indexOf(w) !== -1; }) &&
        (!category || (el.dataset.categories || "").indexOf("|" + category + "|") !== -1);
      el.hidden = !ok;
      if (ok) shown++;
    });
    document.querySelectorAll(".course").forEach(function (sec) {
      sec.hidden = !sec.querySelector(".filter-item:not([hidden])");
    });
    var empty = document.querySelector(".empty-note");
    if (empty) empty.hidden = shown > 0;
  }
  if (input) input.addEventListener("input", apply);
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      category = chip.dataset.category;
      chips.forEach(function (c) { c.setAttribute("aria-pressed", String(c === chip)); });
      var url = new URL(location.href);
      if (category) url.searchParams.set("category", category); else url.searchParams.delete("category");
      history.replaceState(null, "", url);
      apply();
    });
  });
  var initial = new URLSearchParams(location.search).get("category");
  if (initial) {
    var match = chips.filter(function (c) { return c.dataset.category === initial; })[0];
    if (match) match.click();
  }
})();
