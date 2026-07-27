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

  // Send Invoice: the sponsorship level follows the conference, and choosing a
  // level fills the line item and amount. Progressive enhancement only — the
  // server resolves the same pair from conference_id/tier_id, so a submit
  // without any of this still produces the right invoice.
  var levelsHost = document.querySelector("[data-invoice-levels]");
  if (levelsHost) {
    var levels = {};
    try {
      levels = JSON.parse(levelsHost.dataset.invoiceLevels || "{}");
    } catch (err) {
      levels = {};
    }
    var confSel = document.querySelector("[data-invoice-conference]");
    var levelSel = document.querySelector("[data-invoice-level]");
    var amount = document.querySelector("[data-invoice-amount]");
    var note = document.querySelector("[data-invoice-amount-note]");
    var customItem = document.querySelector("[data-invoice-custom-item]");

    // The price the chosen level costs, so we can tell "untouched" (keep it in
    // step with the level) from "deliberately overridden" (leave it alone).
    var levelPrice = "";

    function currentLevel() {
      var list = levels[confSel.value] || [];
      for (var i = 0; i < list.length; i++) {
        if (list[i].id === levelSel.value) return list[i];
      }
      return null;
    }

    function applyLevel() {
      var lvl = currentLevel();
      var custom = !lvl;
      if (customItem) customItem.hidden = !custom;
      var itemInput = customItem && customItem.querySelector("input");
      if (itemInput) itemInput.disabled = !custom;

      if (!lvl) {
        if (note) note.textContent = "";
        levelPrice = "";
        return;
      }
      // Only overwrite an amount the sender hasn't deliberately changed.
      if (amount && (amount.value === "" || amount.value === levelPrice)) {
        amount.value = lvl.amount;
      }
      levelPrice = lvl.amount;
      if (note) {
        note.textContent = lvl.amount
          ? "Level price " + lvl.amount + " — edit to bill a negotiated amount."
          : "This level has no price set; enter the amount to bill.";
      }
    }

    function fillLevels() {
      var list = levels[confSel.value] || [];
      // A new conference has different levels, so the old choice cannot carry
      // over; fall back to its first level (or custom when it sells none).
      var previous = "";
      levelSel.innerHTML = "";
      list.forEach(function (lvl) {
        var opt = document.createElement("option");
        opt.value = lvl.id;
        opt.textContent = lvl.name + (lvl.amount ? " — " + lvl.amount : "");
        levelSel.appendChild(opt);
      });
      var custom = document.createElement("option");
      custom.value = "custom";
      custom.textContent = list.length ? "Other / custom…" : "Custom item…";
      levelSel.appendChild(custom);
      levelSel.value = previous && levelSel.querySelector('[value="' + previous + '"]')
        ? previous : (list.length ? list[0].id : "custom");
      applyLevel();
    }

    if (confSel && levelSel) {
      confSel.addEventListener("change", fillLevels);
      levelSel.addEventListener("change", applyLevel);
      // The options are already rendered server-side for the current
      // conference, so only sync the dependent fields on load — rebuilding
      // here would discard a level restored after a validation error.
      applyLevel();
    }
  }
})();
