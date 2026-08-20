// Show the URL box only when a link target is set to "External link".
// The box ships hidden from the server, so this is what reveals it — the
// admin panel already requires JS (the chunked backup transfer, the form
// builder), and an always-visible URL field on every row reads as clutter.
// The server never trusts the visibility either way: it reads the URL only
// when the dropdown asks for it.
(function () {
  "use strict";

  var EXTERNAL = "__external__";

  function sync(picker) {
    var select = picker.querySelector("[data-target-select]");
    var url = picker.querySelector("[data-target-url]");
    if (!select || !url) return;
    url.hidden = select.value !== EXTERNAL;
  }

  document.querySelectorAll("[data-target-picker]").forEach(function (picker) {
    sync(picker);
    var select = picker.querySelector("[data-target-select]");
    if (select) {
      select.addEventListener("change", function () { sync(picker); });
    }
  });
})();
