(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var sel = document.querySelectorAll("select[data-country-select]");
    if (!sel.length) return;

    fetch("/static/data/countries.json")
      .then(function (r) { return r.json(); })
      .then(function (countries) {
        sel.forEach(function (orig) {
          var wrapper = document.createElement("div");
          wrapper.className = "country-select";
          wrapper.style.position = "relative";

          var input = document.createElement("input");
          input.className = "input";
          input.type = "text";
          input.placeholder = "Search countries…";
          input.autocomplete = "off";

          var dropdown = document.createElement("div");
          dropdown.className = "country-select-dropdown";

          var hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = orig.name;
          hidden.value = orig.value;

          var selectedVal = orig.dataset.value || orig.value;

          if (selectedVal) {
            var match = countries.find(function (c) { return c.name === selectedVal; });
            if (match) input.value = match.name + " (" + match.code + ")";
            else input.value = selectedVal;
          }

          function buildDropdown(filter) {
            var f = (filter || "").toLowerCase().trim();
            dropdown.innerHTML = "";
            var shown = 0;
            countries.forEach(function (c) {
              var label = c.name + (c.code ? " (" + c.code + ")" : "");
              if (f) {
                var found = c.name.toLowerCase().indexOf(f) !== -1
                  || (c.code && c.code.toLowerCase().indexOf(f) !== -1)
                  || c.alt.some(function (a) { return a.toLowerCase().indexOf(f) !== -1; });
                if (!found) return;
              }
              shown++;
              var div = document.createElement("div");
              div.className = "country-select-item";
              div.textContent = label;
              div.addEventListener("mousedown", function (e) {
                e.preventDefault();
                input.value = label;
                hidden.value = c.name;
                dropdown.style.display = "none";
              });
              dropdown.appendChild(div);
            });
            if (!shown) {
              var nd = document.createElement("div");
              nd.className = "country-select-item muted";
              nd.textContent = "No countries found";
              dropdown.appendChild(nd);
            }
          }

          input.addEventListener("focus", function () {
            buildDropdown(input.value);
            dropdown.style.display = "";
          });

          input.addEventListener("input", function () {
            buildDropdown(input.value);
            dropdown.style.display = "";
          });

          input.addEventListener("blur", function () {
            setTimeout(function () { dropdown.style.display = "none"; }, 150);
          });

          input.addEventListener("keydown", function (e) {
            if (e.key === "Escape") { dropdown.style.display = "none"; input.blur(); }
            if (e.key === "Enter") {
              var first = dropdown.querySelector(".country-select-item:not(.muted)");
              if (first) first.dispatchEvent(new MouseEvent("mousedown"));
              e.preventDefault();
            }
          });

          orig.parentNode.insertBefore(wrapper, orig);
          wrapper.appendChild(input);
          wrapper.appendChild(dropdown);
          wrapper.appendChild(hidden);
          orig.remove();
        });
      });
  });
})();
