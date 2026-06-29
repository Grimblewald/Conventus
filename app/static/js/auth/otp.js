(function () {
  "use strict";

  var box = document.querySelector(".otp-box");
  var hidden = document.getElementById("otp-hidden");
  if (!box || !hidden) return;

  var digits = Array.prototype.slice.call(box.querySelectorAll(".otp-digit"));

  function sync() {
    hidden.value = digits.map(function (d) { return d.value; }).join("");
  }

  digits.forEach(function (d, i) {
    d.addEventListener("input", function () {
      d.value = d.value.replace(/\D/g, "").slice(0, 1);
      if (d.value && i < digits.length - 1) digits[i + 1].focus();
      sync();
    });
    d.addEventListener("keydown", function (e) {
      if (e.key === "Backspace" && !d.value && i > 0) digits[i - 1].focus();
    });
    d.addEventListener("paste", function (e) {
      e.preventDefault();
      var t = (e.clipboardData || window.clipboardData).getData("text");
      var nums = t.replace(/\D/g, "").slice(0, 6).split("");
      nums.forEach(function (n, j) { if (digits[j]) digits[j].value = n; });
      sync();
      var next = digits[Math.min(nums.length, digits.length - 1)];
      if (next) next.focus();
    });
  });

  if (digits[0]) digits[0].focus();
})();
