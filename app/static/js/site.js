/* Site-wide JS: just OTP digit box. Everything else (live palette preview,
   admin sub-nav) is wired inline in the templates that need it. */

(function () {
  "use strict";

  // ---- Attach CSRF token to every fetch() that's same-origin & state-changing.
  const csrf = document.querySelector('meta[name="csrf-token"]');
  if (csrf && window.fetch) {
    const original = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      const method = (init.method || "GET").toUpperCase();
      if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
        init.headers = init.headers || {};
        init.headers["X-CSRFToken"] = csrf.getAttribute("content");
      }
      return original(input, init);
    };
  }

  // ---- OTP digit box ----
  document.addEventListener("DOMContentLoaded", () => {
    const box = document.querySelector(".otp-box");
    const hidden = document.querySelector("#otp-hidden");
    if (!box || !hidden) return;
    const digits = Array.from(box.querySelectorAll(".otp-digit"));

    function sync() {
      hidden.value = digits.map(d => d.value).join("");
    }
    digits.forEach((d, i) => {
      d.addEventListener("input", () => {
        d.value = d.value.replace(/\D/g, "").slice(0, 1);
        if (d.value && i < digits.length - 1) digits[i + 1].focus();
        sync();
      });
      d.addEventListener("keydown", (e) => {
        if (e.key === "Backspace" && !d.value && i > 0) digits[i - 1].focus();
      });
      d.addEventListener("paste", (e) => {
        e.preventDefault();
        const t = (e.clipboardData || window.clipboardData).getData("text");
        const nums = t.replace(/\D/g, "").slice(0, 6).split("");
        nums.forEach((n, j) => { if (digits[j]) digits[j].value = n; });
        sync();
        const next = digits[Math.min(nums.length, digits.length - 1)];
        if (next) next.focus();
      });
    });
    if (digits[0]) digits[0].focus();
  });
})();
