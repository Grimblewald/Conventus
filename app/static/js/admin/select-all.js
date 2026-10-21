(function () {
  "use strict";
  // A header checkbox that ticks or clears every checkbox in its table.
  document.querySelectorAll("[data-select-all]").forEach(function (all) {
    var table = all.closest("table");
    all.checked = true;
    all.addEventListener("change", function () {
      table.querySelectorAll("tbody input[type=checkbox]").forEach(function (box) {
        box.checked = all.checked;
      });
    });
  });
})();
