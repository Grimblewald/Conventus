(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("abstract-form");
    var rowsContainer = document.getElementById("author-rows");
    var addBtn = document.getElementById("add-author-btn");
    var tmpl = document.getElementById("author-row-tmpl");
    var hidden = document.getElementById("authors-hidden");
    var presentingSelect = document.getElementById("presenting-author-select");

    if (!form || !rowsContainer || !addBtn || !tmpl || !hidden) return;

    // ====================================================================
    // Author rows
    // ====================================================================

    function gatherAffiliations() {
      var map = [];
      rowsContainer.querySelectorAll(".author-row").forEach(function (row) {
        var sel = row.querySelector("[data-author-affil]");
        if (!sel) return;
        for (var i = 1; i < sel.options.length; i++) {
          var val = sel.options[i].value;
          if (val && val !== "__new__" && map.indexOf(val) === -1) {
            map.push(val);
          }
        }
        var newInput = row.querySelector("[data-author-affil-new]");
        if (newInput && newInput.style.display !== "none") {
          var nv = newInput.value.trim();
          if (nv && map.indexOf(nv) === -1) {
            map.push(nv);
          }
        }
      });
      return map;
    }

    function refreshAffilDropdowns() {
      var affils = gatherAffiliations();
      rowsContainer.querySelectorAll(".author-row").forEach(function (row) {
        var sel = row.querySelector("[data-author-affil]");
        if (!sel) return;
        var current = sel.value;
        while (sel.options.length > 2) sel.remove(2);
        affils.forEach(function (a) {
          var opt = document.createElement("option");
          opt.value = a;
          opt.textContent = a;
          sel.appendChild(opt);
        });
        if (current && current !== "__new__") {
          for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value === current) {
              sel.value = current;
              break;
            }
          }
        }
      });
    }

    function refreshPresentingAuthorSelect() {
      if (!presentingSelect) return;
      var current = presentingSelect.value;
      presentingSelect.innerHTML = '<option value="">— select —</option>';
      var rows = rowsContainer.querySelectorAll(".author-row");
      rows.forEach(function (row, idx) {
        var nameInput = row.querySelector("[data-author-name]");
        var name = nameInput ? nameInput.value.trim() : "";
        if (!name) return;
        var opt = document.createElement("option");
        opt.value = idx;
        opt.textContent = name;
        presentingSelect.appendChild(opt);
      });
      for (var i = 0; i < presentingSelect.options.length; i++) {
        if (presentingSelect.options[i].value === current) {
          presentingSelect.value = current;
          return;
        }
      }
    }

    function serialize() {
      var lines = [];
      var affilIndex = {};
      var nextIdx = 1;
      var rows = rowsContainer.querySelectorAll(".author-row");

      rows.forEach(function (row) {
        var sel = row.querySelector("[data-author-affil]");
        var newInput = row.querySelector("[data-author-affil-new]");
        var affilName = "";
        if (sel && sel.value === "__new__" && newInput) {
          affilName = newInput.value.trim();
        } else if (sel && sel.value) {
          affilName = sel.value;
        }
        if (affilName && !(affilName in affilIndex)) {
          affilIndex[affilName] = nextIdx++;
        }
      });

      rows.forEach(function (row) {
        var nameInput = row.querySelector("[data-author-name]");
        var sel = row.querySelector("[data-author-affil]");
        var newInput = row.querySelector("[data-author-affil-new]");
        var name = nameInput ? nameInput.value.trim() : "";
        if (!name) return;
        var affilName = "";
        if (sel && sel.value === "__new__" && newInput) {
          affilName = newInput.value.trim();
        } else if (sel && sel.value) {
          affilName = sel.value;
        }
        var idx = affilIndex[affilName] || 0;
        if (affilName && idx) {
          lines.push(name + "|" + idx + "|" + affilName);
        } else {
          lines.push(name + "||");
        }
      });

      hidden.value = lines.join("\n");
    refreshPresentingAuthorSelect();

    // Populate existing references when editing a draft
    var refsData = document.getElementById("references-data");
    if (refsData && refContainer) {
      try {
        var existingRefs = JSON.parse(refsData.textContent);
        if (Array.isArray(existingRefs) && existingRefs.length > 0) {
          refContainer.innerHTML = "";
          existingRefs.forEach(function (r) {
            var clone = refTmpl.content.firstElementChild.cloneNode(true);
            var doiInp = clone.querySelector("[data-ref-doi]");
            if (doiInp) doiInp.value = r.doi || "";
            refContainer.appendChild(clone);
          });
          reindexRefs();
          validateReferences();
        }
      } catch (e) { /* ignore */ }
    }
    }

    function bindRow(row) {
      var sel = row.querySelector("[data-author-affil]");
      var newInput = row.querySelector("[data-author-affil-new]");
      var removeBtn = row.querySelector(".js-remove-author");
      var nameInput = row.querySelector("[data-author-name]");

      if (sel) {
        sel.addEventListener("change", function () {
          if (sel.value === "__new__") {
            if (newInput) { newInput.style.display = ""; newInput.focus(); }
          } else {
            if (newInput) { newInput.style.display = "none"; newInput.value = ""; }
            refreshAffilDropdowns();
          }
        });
      }

      if (newInput) {
        newInput.addEventListener("blur", function () {
          var v = newInput.value.trim();
          if (v && sel.value === "__new__") {
            refreshAffilDropdowns();
            for (var j = 0; j < sel.options.length; j++) {
              if (sel.options[j].value === v) { sel.value = v; break; }
            }
            newInput.style.display = "none";
          }
        });
      }

      if (removeBtn) {
        removeBtn.addEventListener("click", function () {
          var allRows = rowsContainer.querySelectorAll(".author-row");
          if (allRows.length <= 1) return;
          row.parentNode.removeChild(row);
          refreshAffilDropdowns();
          serialize();
        });
      }

      if (nameInput) {
        nameInput.addEventListener("input", function () { serialize(); });
      }
    }

    function addRow() {
      var clone = tmpl.content.firstElementChild.cloneNode(true);
      rowsContainer.appendChild(clone);
      refreshAffilDropdowns();
      bindRow(clone);
    }

    form.addEventListener("submit", function () {
      serialize();
      var allRows = rowsContainer.querySelectorAll(".author-row");
      if (allRows.length === 0) {
        alert("Please add at least one author.");
        return false;
      }
      return true;
    });

    if (addBtn) addBtn.addEventListener("click", addRow);
    addRow();

    var authorsData = document.getElementById("authors-data");
    if (authorsData) {
      try {
        var existing = JSON.parse(authorsData.textContent);
        if (existing) {
          rowsContainer.innerHTML = "";
          existing.split("\n").forEach(function (line) {
            var parts = line.split("|");
            if (parts.length < 1) return;
            var clone = tmpl.content.firstElementChild.cloneNode(true);
            var nameInp = clone.querySelector("[data-author-name]");
            var affilSel = clone.querySelector("[data-author-affil]");
            if (nameInp && parts[0]) nameInp.value = parts[0];
            if (affilSel && parts[2]) {
              var opt = document.createElement("option");
              opt.value = parts[2];
              opt.textContent = parts[2];
              opt.selected = true;
              affilSel.appendChild(opt);
            }
            rowsContainer.appendChild(clone);
            bindRow(clone);
          });
          refreshAffilDropdowns();
          serialize();
        }
      } catch (e) { /* ignore */ }
    }

    refreshPresentingAuthorSelect();

    // ====================================================================
    // Word counting
    // ====================================================================

    function countWords(text) {
      return (text || "").trim().split(/\s+/).filter(Boolean).length;
    }

    var titleInput = document.getElementById("title");
    var titleWC = document.getElementById("title-word-count");
    var bodyInput = document.getElementById("body");
    var bodyWC = document.getElementById("body-word-count");

    function updateTitleCount() {
      if (!titleInput || !titleWC) return;
      var wc = countWords(titleInput.value);
      titleWC.textContent = "(" + wc + " word" + (wc !== 1 ? "s" : "") + ")";
      titleWC.style.color = wc > 15 ? "var(--c-accent)" : "";
    }

    function updateBodyCount() {
      if (!bodyInput || !bodyWC) return;
      var wc = countWords(bodyInput.value);
      bodyWC.textContent = "(" + wc + " / 300 word" + (wc !== 1 ? "s" : "") + ")";
      bodyWC.style.color = wc > 300 ? "var(--c-accent)" : "";
    }

    if (titleInput) titleInput.addEventListener("input", updateTitleCount);
    if (bodyInput) bodyInput.addEventListener("input", updateBodyCount);
    updateTitleCount();
    updateBodyCount();

    // ====================================================================
    // References
    // ====================================================================

    var refContainer = document.getElementById("reference-rows");
    var addRefBtn = document.getElementById("add-reference-btn");
    var refTmpl = document.getElementById("reference-row-tmpl");
    var refMsg = document.getElementById("reference-validation-msg");

    function parseBodyRefs() {
      if (!bodyInput) return [];
      var matches = bodyInput.value.match(/\[(\d+)\]/g) || [];
      var nums = [];
      matches.forEach(function (m) {
        var n = parseInt(m.replace(/[\[\]]/g, ""), 10);
        if (nums.indexOf(n) === -1) nums.push(n);
      });
      return nums.sort(function (a, b) { return a - b; });
    }

    function getRefDOIs() {
      var dois = [];
      if (!refContainer) return dois;
      refContainer.querySelectorAll(".reference-row").forEach(function (row) {
        var inp = row.querySelector("[data-ref-doi]");
        if (inp) dois.push(inp.value.trim());
      });
      return dois;
    }

    function reindexRefs() {
      if (!refContainer) return;
      refContainer.querySelectorAll(".reference-row").forEach(function (row, idx) {
        row.querySelector(".ref-key").textContent = "[" + (idx + 1) + "]";
      });
    }

    function validateReferences() {
      if (!refMsg) return;
      var bodyRefs = parseBodyRefs();
      var dois = getRefDOIs();
      var maxKey = dois.filter(function (d) { return d !== ""; }).length;
      var errors = [];

      bodyRefs.forEach(function (n) {
        if (n > maxKey) errors.push("Citation [\u200B" + n + "\u200B] in text has no matching reference.");
      });
      for (var k = 1; k <= maxKey; k++) {
        if (bodyRefs.indexOf(k) === -1) errors.push("Reference [\u200B" + k + "\u200B] is not cited in the abstract text.");
      }

      if (errors.length > 0) {
        refMsg.style.display = "";
        refMsg.style.color = "var(--c-accent)";
        refMsg.textContent = errors.join(" ");
      } else if (maxKey > 0) {
        refMsg.style.display = "";
        refMsg.style.color = "";
        refMsg.textContent = maxKey + " reference" + (maxKey !== 1 ? "s" : "") + " — all cited in text.";
      } else {
        refMsg.style.display = "none";
      }
    }

    function addRefRow() {
      if (!refContainer || !refTmpl) return;
      var clone = refTmpl.content.firstElementChild.cloneNode(true);
      refContainer.appendChild(clone);
      reindexRefs();
      var doiInput = clone.querySelector("[data-ref-doi]");
      var removeBtn = clone.querySelector(".js-remove-ref");
      if (removeBtn) {
        removeBtn.addEventListener("click", function () {
          clone.parentNode.removeChild(clone);
          reindexRefs();
          validateReferences();
        });
      }
      if (doiInput) {
        doiInput.addEventListener("input", validateReferences);
      }
    }

    if (addRefBtn) addRefBtn.addEventListener("click", addRefRow);
    if (bodyInput) bodyInput.addEventListener("input", validateReferences);

    // Initialize existing refs from edit form if any
    if (refContainer) {
      var existingRefs = refContainer.querySelectorAll("[data-ref-doi]");
      if (existingRefs.length > 0) {
        existingRefs.forEach(function (inp) {
          inp.addEventListener("input", validateReferences);
        });
      }
    }
  });
})();
