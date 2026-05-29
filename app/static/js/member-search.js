(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var wrap = document.querySelector(".search-select[data-search-url]");
    if (!wrap) return;

    var url = wrap.dataset.searchUrl;
    var input = wrap.querySelector(".search-select-input");
    var hidden = wrap.querySelector("input[type=hidden]");
    var dropdown = wrap.querySelector(".search-select-dropdown");
    if (!input || !hidden || !dropdown) return;

    var timer = null;
    var results = [];
    var selectedIndex = -1;
    var selectedName = "";

    function clearDropdown() {
      dropdown.innerHTML = "";
      dropdown.style.display = "none";
      results = [];
      selectedIndex = -1;
    }

    function selectResult(item) {
      hidden.value = item.id;
      selectedName = item.full_name
        ? item.full_name + " (" + item.email + ")"
        : item.email;
      input.value = selectedName;
      clearDropdown();
    }

    function renderDropdown(items) {
      results = items;
      selectedIndex = -1;
      if (!items.length) {
        dropdown.innerHTML =
          '<div class="search-select-item muted">No matches</div>';
        dropdown.style.display = "block";
        return;
      }
      var html = "";
      items.forEach(function (item, i) {
        var label = item.full_name
          ? item.full_name + " · " + item.email
          : item.email;
        html +=
          '<div class="search-select-item" data-idx="' +
          i +
          '">' +
          label +
          ' <span class="muted" style="font-size:11px;">(' +
          item.role_name +
          ")</span></div>";
      });
      dropdown.innerHTML = html;
      dropdown.style.display = "block";
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (q.length < 2) {
        clearDropdown();
        if (hidden.value && input.value !== selectedName) {
          hidden.value = "";
          selectedName = "";
        }
        return;
      }
      clearTimeout(timer);
      timer = setTimeout(function () {
        fetch(url + "?q=" + encodeURIComponent(q))
          .then(function (r) {
            if (!r.ok) throw new Error("search failed");
            return r.json();
          })
          .then(function (data) {
            renderDropdown(data);
          })
          .catch(function () {
            clearDropdown();
          });
      }, 300);
    });

    input.addEventListener("keydown", function (e) {
      if (dropdown.style.display === "none" || !results.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        selectedIndex = Math.min(selectedIndex + 1, results.length - 1);
        highlightSelected();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        selectedIndex = Math.max(selectedIndex - 1, 0);
        highlightSelected();
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < results.length) {
          selectResult(results[selectedIndex]);
        }
      } else if (e.key === "Escape") {
        clearDropdown();
      }
    });

    function highlightSelected() {
      var items = dropdown.querySelectorAll(".search-select-item");
      items.forEach(function (el) {
        el.classList.remove("active");
      });
      if (selectedIndex >= 0 && items[selectedIndex]) {
        items[selectedIndex].classList.add("active");
        items[selectedIndex].scrollIntoView({ block: "nearest" });
      }
    }

    dropdown.addEventListener("click", function (e) {
      var item = e.target.closest(".search-select-item");
      if (!item) return;
      var idx = parseInt(item.dataset.idx, 10);
      if (!isNaN(idx) && results[idx]) {
        selectResult(results[idx]);
      }
    });

    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target)) {
        clearDropdown();
      }
    });

    input.addEventListener("focus", function () {
      if (results.length) {
        dropdown.style.display = "block";
      }
    });
  });
})();
