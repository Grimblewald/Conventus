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

          var MAX_SHOWN = 5;

          function buildDropdown(filter) {
            var f = (filter || "").toLowerCase().trim();
            dropdown.innerHTML = "";

            var matches = [];
            countries.forEach(function (c) {
              if (f) {
                var found = c.name.toLowerCase().indexOf(f) !== -1
                  || (c.code && c.code.toLowerCase().indexOf(f) !== -1)
                  || c.alt.some(function (a) { return a.toLowerCase().indexOf(f) !== -1; });
                if (!found) return;
              }
              matches.push(c);
            });

            if (!matches.length) {
              var nd = document.createElement("div");
              nd.className = "country-select-item muted";
              nd.textContent = "No countries found";
              dropdown.appendChild(nd);
              return;
            }

            var total = matches.length;
            var shown = matches.slice(0, MAX_SHOWN);

            shown.forEach(function (c) {
              var label = c.name + (c.code ? " (" + c.code + ")" : "");
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

            if (total > MAX_SHOWN) {
              var footer = document.createElement("div");
              footer.className = "country-select-footer";
              footer.textContent = "Top " + MAX_SHOWN + " of " + total + " matches \u2014 keep typing";
              dropdown.appendChild(footer);
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
