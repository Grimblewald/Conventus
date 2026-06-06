(function () {
  "use strict";

  // ---- Attach CSRF token to every fetch() that's same-origin & state-changing.
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

  // ---- Generic confirmation dialog for data-confirm ----
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

  // ---- DOM ready handlers ----
  document.addEventListener("DOMContentLoaded", function () {
    // Permissions: role-picker reload
    var rolePicker = document.getElementById("role_picker");
    if (rolePicker) {
      rolePicker.addEventListener("change", function () {
        var url = new URL(window.location.href);
        url.searchParams.set("role", this.value);
        window.location.href = url.toString();
      });
    }

    // Conference edit: toggle reopen date visibility
    var notAbs = document.getElementById("not_accepting_abstracts");
    var absRow = document.getElementById("abstracts_reopen_row");
    if (notAbs && absRow) {
      notAbs.addEventListener("change", function () {
        absRow.style.display = this.checked ? "" : "none";
      });
    }
    var notReg = document.getElementById("not_accepting_registrations");
    var regRow = document.getElementById("registrations_reopen_row");
    if (notReg && regRow) {
      notReg.addEventListener("change", function () {
        regRow.style.display = this.checked ? "" : "none";
      });
    }

    // --- Dynamic row management for conference edit ---

    function markRowDeleted(row) {
      var hidden = row.querySelector('input[type="hidden"]');
      if (hidden) hidden.value = "1";
      row.style.display = "none";
    }

    function removeNewRow(row) {
      row.parentNode.removeChild(row);
    }

    // --- Price tiers ---
    var tierTmpl = document.getElementById("tier-row-tmpl");
    var tiersTbody = document.getElementById("tiers-tbody");
    var addTierBtn = document.getElementById("add-tier-btn");

    if (addTierBtn && tierTmpl && tiersTbody) {
      addTierBtn.addEventListener("click", function () {
        var clone = tierTmpl.content.firstElementChild.cloneNode(true);
        tiersTbody.appendChild(clone);
      });
    }

    if (tiersTbody) {
      tiersTbody.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        if (btn.classList.contains("js-remove-tier")) {
          var row = btn.closest("tr");
          if (row && row.dataset.tierId) {
            markRowDeleted(row);
          }
        } else if (btn.classList.contains("js-remove-row")) {
          removeNewRow(btn.closest("tr"));
        }
      });
    }

    // --- Sponsor tiers ---
    var stierTmpl = document.getElementById("stier-row-tmpl");
    var stiersTbody = document.getElementById("sponsor-tiers-tbody");
    var addStierBtn = document.getElementById("add-stier-btn");
    var noTiersMsg = document.getElementById("no-sponsor-tiers-msg");

    if (addStierBtn && stierTmpl) {
      addStierBtn.addEventListener("click", function () {
        var clone = stierTmpl.content.firstElementChild.cloneNode(true);
        if (stiersTbody) {
          stiersTbody.appendChild(clone);
        } else {
          var table = document.createElement("table");
          table.className = "table";
          table.innerHTML = '<thead><tr><th style="width:64px">Order</th><th>Name</th><th>Sponsors</th><th style="width:70px"></th></tr></thead><tbody id="sponsor-tiers-tbody"></tbody>';
          var tbody = table.querySelector("tbody");
          tbody.appendChild(clone);
          stiersTbody = tbody;
          addStierBtn.parentNode.insertBefore(table, addStierBtn);
          if (noTiersMsg) noTiersMsg.style.display = "none";
        }
      });
    }

    var spTierContainer = stiersTbody || document.getElementById("sponsor-tiers-tbody");
    if (spTierContainer) {
      spTierContainer.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        if (btn.classList.contains("js-remove-stier")) {
          var row = btn.closest("tr");
          if (row && row.dataset.tierId) {
            markRowDeleted(row);
          }
        } else if (btn.classList.contains("js-remove-row")) {
          removeNewRow(btn.closest("tr"));
        } else if (btn.classList.contains("js-add-sponsor")) {
          var tierId = btn.dataset.tierId;
          var sponsTmpl = document.getElementById("sponsor-row-tmpl");
          var sponsorList = btn.parentNode.querySelector(".sponsor-list");
          if (!sponsTmpl || !sponsorList || !tierId) return;
          var existingNew = sponsorList.querySelectorAll(".sponsor-row--new");
          var idx = existingNew.length;
          var clone = sponsTmpl.content.firstElementChild.cloneNode(true);
          clone.querySelectorAll("input").forEach(function (inp) {
            inp.name = inp.name.replace("PLACEHOLDER", tierId + "_" + idx);
            if (inp.id) inp.id = inp.id.replace("PLACEHOLDER", tierId + "_" + idx);
          });
          clone.querySelectorAll("label").forEach(function (lbl) {
            if (lbl.htmlFor) lbl.htmlFor = lbl.htmlFor.replace("PLACEHOLDER", tierId + "_" + idx);
          });
          sponsorList.appendChild(clone);
        }
      });
    }

    document.querySelectorAll(".sponsor-list").forEach(function (list) {
      list.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        if (btn.classList.contains("js-remove-sponsor")) {
          var row = btn.closest(".sponsor-row");
          if (row && row.dataset.sponsorId) {
            markRowDeleted(row);
          }
        } else if (btn.classList.contains("js-remove-row")) {
          removeNewRow(btn.closest(".sponsor-row--new"));
        }
      });
    });

    // Nav: delete button (clear all row ids before submit via separate form)
    document.querySelectorAll("[data-nav-delete]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.form.querySelectorAll("input[name=id]").forEach(function (el) {
          el.value = "";
        });
      });
    });

    // Members: per-row role dropdown
    document.querySelectorAll("select.role-select[data-user-id]").forEach(function (sel) {
      sel.addEventListener("change", function () {
        var userId = this.dataset.userId;
        var newRole = this.value;
        var original = this.dataset.originalRole;
        if (newRole === original) return;

        fetch("/admin/api/users/" + userId + "/role", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: newRole }),
        })
          .then(function (r) {
            if (!r.ok) return r.json().then(function (d) { throw new Error(d.error || "Failed"); });
            return r.json();
          })
          .then(function () {
            sel.dataset.originalRole = newRole;
            sel.classList.add("flash-ok");
            setTimeout(function () { sel.classList.remove("flash-ok"); }, 1500);
          })
          .catch(function (err) {
            sel.value = original;
            sel.classList.add("flash-err");
            setTimeout(function () { sel.classList.remove("flash-err"); }, 1500);
          });
      });
    });

    // ---- OTP digit box ----
    var box = document.querySelector(".otp-box");
    var hidden = document.getElementById("otp-hidden");
    if (box && hidden) {
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
    }
  });
})();
