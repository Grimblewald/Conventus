(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("abstract-form");
    var rowsContainer = document.getElementById("author-rows");
    var addBtn = document.getElementById("add-author-btn");
    var tmpl = document.getElementById("author-row-tmpl");
    var hidden = document.getElementById("authors-hidden");

    if (!form || !rowsContainer || !addBtn || !tmpl || !hidden) return;

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

    addBtn.addEventListener("click", addRow);
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
  });
})();
