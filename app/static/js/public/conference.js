(function () {
  "use strict";

  // --- Organising Committee modal (public conference detail page) ---
  var ocToggle = document.getElementById("oc-toggle-btn");
  var ocModal = document.getElementById("oc-modal");
  var ocClose = document.getElementById("oc-modal-close");

  if (ocToggle && ocModal) {
    ocToggle.addEventListener("click", function () { ocModal.style.display = ""; });
    ocClose.addEventListener("click", function () { ocModal.style.display = "none"; });
    ocModal.addEventListener("click", function (e) { if (e.target === ocModal) ocModal.style.display = "none"; });
  }

  // --- Speaker talk title expand toggle ---
  document.addEventListener("click", function (e) {
    var toggle = e.target.closest(".speaker-talk-toggle");
    if (!toggle) return;
    var parent = toggle.parentElement;
    var full = parent.querySelector(".speaker-talk-full");
    var text = parent.querySelector(".speaker-talk-text");
    if (!full || !text) return;
    var expanded = full.style.display !== "none";
    full.style.display = expanded ? "none" : "inline";
    text.style.display = expanded ? "inline" : "none";
    toggle.textContent = expanded ? " ▾" : " ▴";
  });
})();
