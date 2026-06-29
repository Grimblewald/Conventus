(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-nav-delete]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.form.querySelectorAll("input[name=id]").forEach(function (el) {
          el.value = "";
        });
      });
    });
  });
})();
