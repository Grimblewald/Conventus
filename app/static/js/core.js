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
})();
