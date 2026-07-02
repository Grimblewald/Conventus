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

          var input = document.createElement("input");
          input.className = "input";
          input.type = "text";
          input.placeholder = "Type to search…";
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

          function scoreCountry(c, needle) {
            if (!needle) return 0;
            var nameLow = c.name.toLowerCase();
            if (nameLow === needle) return 4;
            if (nameLow.indexOf(needle) === 0) return 3;
            if (nameLow.indexOf(needle) !== -1) return 2;
            if (c.code && c.code.toLowerCase().indexOf(needle) !== -1) return 1;
            return 0;
          }

          function sortMatches(matches, needle) {
            if (!needle) return matches;
            var n = needle.toLowerCase().trim();
            return matches.sort(function (a, b) {
              var sa = scoreCountry(a, n);
              var sb = scoreCountry(b, n);
              if (sa !== sb) return sb - sa;
              return a.name.localeCompare(b.name);
            });
          }

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

            matches = sortMatches(matches, f);
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

          function showDropdown() {
            buildDropdown(input.value);
            dropdown.style.display = "block";
          }

          function hideDropdown() {
            dropdown.style.display = "none";
          }

          input.addEventListener("focus", showDropdown);
          input.addEventListener("input", showDropdown);

          input.addEventListener("blur", function () {
            setTimeout(hideDropdown, 150);
          });

          input.addEventListener("keydown", function (e) {
            if (e.key === "Escape") { hideDropdown(); input.blur(); }
            if (e.key === "Enter") {
              var first = dropdown.querySelector(".country-select-item:not(.muted)");
              if (first) first.dispatchEvent(new MouseEvent("mousedown"));
              e.preventDefault();
            }
            if (e.key === "ArrowDown") {
              e.preventDefault();
              var items = dropdown.querySelectorAll(".country-select-item:not(.muted)");
              if (items.length) items[0].dispatchEvent(new MouseEvent("mousedown"));
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
