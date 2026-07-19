(function () {
  "use strict";

  var csrf = document.querySelector('meta[name="csrf-token"]');
  if (csrf && window.fetch) {
    var original = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      var method = (init.method || "GET").toUpperCase();
      if (["GET", "HEAD", "OPTIONS"].indexOf(method) === -1) {
        init.headers = init.headers || {};
        init.headers["X-CSRFToken"] = csrf.getAttribute("content");
      }
      return original(input, init);
    };
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.dataset.confirm) return;
    if (!window.confirm(form.dataset.confirm)) {
      e.preventDefault();
    }
  });

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-confirm]");
    if (!btn) return;
    if (!window.confirm(btn.dataset.confirm)) {
      e.preventDefault();
    }
  });

  // CSP forbids inline event handlers — these delegated listeners back the
  // data-* attributes templates use instead.

  // <span data-toggle-next> — show/hide its previous sibling (used for the
  // committee card extra-affiliations toggle).
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-toggle-next]");
    if (!t) return;
    var more = t.parentElement.querySelector(t.dataset.toggleNext);
    if (!more) return;
    var opening = more.style.display === "none";
    more.style.display = opening ? "" : "none";
    t.textContent = opening ? " ▴" : " ▾";
  });

  // <input data-output="id"> — mirror the control's value into an element.
  document.addEventListener("input", function (e) {
    var src = e.target.closest("[data-output]");
    if (!src) return;
    var out = document.getElementById(src.dataset.output);
    if (out) out.textContent = src.value;
  });

  // <button data-reveal="id"> — un-hide an element.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-reveal]");
    if (!btn) return;
    var el = document.getElementById(btn.dataset.reveal);
    if (el) el.style.display = "block";
  });

  // <select data-jump> — navigate to the selected option's value.
  document.addEventListener("change", function (e) {
    var sel = e.target.closest("select[data-jump]");
    if (!sel || !sel.value) return;
    window.location = sel.value;
  });
})();
