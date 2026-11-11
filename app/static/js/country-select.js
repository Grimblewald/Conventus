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
          // The <select> is removed below, and with it the browser's own
          // required check, so the visible box carries it instead.
          if (orig.required) input.required = true;
          if (orig.id) input.id = orig.id;

          var note = document.createElement("div");
          note.className = "help";
          note.style.color = "#6f2335";
          note.style.display = "none";

          var dropdown = document.createElement("div");
          dropdown.className = "country-select-dropdown";

          var hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = orig.name;

          var selectedVal = orig.dataset.value || orig.value;

          // The stored country arrives in data-value, because the server
          // renders this <select> with no options for the script to fill —
          // so orig.value is always "". Seeding the hidden field from it meant
          // an unchanged country submitted nothing: the box showed
          // "Australia (AU)", the payer left it alone precisely because it
          // looked right, and the save failed on a missing country. Only
          // picking from the dropdown ever set this.
          hidden.value = selectedVal || "";

          if (selectedVal) {
            var match = countries.find(function (c) { return c.name === selectedVal; });
            if (match) {
              input.value = match.name + " (" + match.code + ")";
            } else {
              // A stored value the server would refuse must not ride along
              // unseen behind a box that looks filled in.
              input.value = selectedVal;
              hidden.value = "";
              resolveTyped();
            }
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
              // pointerdown fires before the input's blur on touch devices;
              // mousedown kept for the keyboard-driven dispatchEvent path.
              var select = function (e) {
                e.preventDefault();
                choose(c);
                dropdown.style.display = "none";
              };
              div.addEventListener("pointerdown", select);
              div.addEventListener("mousedown", select);
              dropdown.appendChild(div);
            });

            if (total > MAX_SHOWN) {
              var footer = document.createElement("div");
              footer.className = "country-select-footer";
              footer.textContent = "Top " + MAX_SHOWN + " of " + total + " matches \u2014 keep typing";
              dropdown.appendChild(footer);
            }
          }

          function choose(c) {
            input.value = c.name + (c.code ? " (" + c.code + ")" : "");
            hidden.value = c.name;
            setProblem("");
          }

          function setProblem(msg) {
            input.setCustomValidity(msg);
            note.textContent = msg;
            note.style.display = msg ? "" : "none";
          }

          // Only the hidden field is submitted, and people type a country out
          // in full rather than pick a suggestion. An exact name, code or
          // alternative name counts as picking it.
          function resolveTyped() {
            var t = input.value.trim().toLowerCase();
            if (!t) { setProblem(""); return; }
            var hit = countries.find(function (c) {
              var label = (c.name + (c.code ? " (" + c.code + ")" : "")).toLowerCase();
              return c.name.toLowerCase() === t || label === t
                || (c.code && c.code.toLowerCase() === t)
                || c.alt.some(function (a) { return a.toLowerCase() === t; });
            });
            if (hit) choose(hit);
            else setProblem("Choose a country from the list.");
          }

          function showDropdown() {
            buildDropdown(input.value);
            dropdown.style.display = "block";
          }

          function hideDropdown() {
            dropdown.style.display = "none";
          }

          input.addEventListener("focus", showDropdown);
          input.addEventListener("input", function () {
            // Editing the text unpicks the country, so an old choice can't be
            // submitted under a box that now says something else.
            hidden.value = "";
            setProblem("");
            showDropdown();
          });

          input.addEventListener("blur", function () {
            setTimeout(function () {
              hideDropdown();
              if (!hidden.value) resolveTyped();
            }, 150);
          });
          input.addEventListener("change", function () {
            if (!hidden.value) resolveTyped();
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
          wrapper.appendChild(note);
          orig.remove();

          // Enter or a fast click can submit before blur has resolved the text.
          var form = wrapper.closest("form");
          if (form) {
            form.addEventListener("submit", function (e) {
              if (!hidden.value && input.value.trim()) resolveTyped();
              if (input.value.trim() && !hidden.value) {
                e.preventDefault();
                input.reportValidity();
              }
            });
          }
        });
      });
  });
})();
