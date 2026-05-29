(function () {
  "use strict";

  var el = document.querySelector(".countdown[data-target]");
  if (!el) return;

  var target = new Date(el.dataset.target).getTime();
  var pills = el.querySelectorAll(".pill .n");
  if (pills.length < 4) return;

  function tick() {
    var diff = Math.max(0, target - Date.now());
    var d = Math.floor(diff / 86400000);
    var h = Math.floor((diff / 3600000) % 24);
    var m = Math.floor((diff / 60000) % 60);
    var s = Math.floor((diff / 1000) % 60);
    pills[0].textContent = d;
    pills[1].textContent = String(h).padStart(2, "0");
    pills[2].textContent = String(m).padStart(2, "0");
    pills[3].textContent = String(s).padStart(2, "0");
  }

  tick();
  setInterval(tick, 1000);
})();
