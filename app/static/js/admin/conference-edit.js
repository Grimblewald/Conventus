(function () {
  "use strict";

  function markRowDeleted(row) {
    var hidden = row.querySelector('input[type="hidden"]');
    if (hidden) hidden.value = "1";
    row.style.display = "none";
  }

  function removeNewRow(row) {
    row.parentNode.removeChild(row);
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Reopen date toggles
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

    // Sponsor-tier delegation on a stable parent (not the tbody itself,
    // which may not exist yet when no tiers are configured).
    var confForm = document.getElementById("conf-edit-form");
    if (confForm) {
      confForm.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        if (btn.classList.contains("js-remove-stier")) {
          var row = btn.closest("tr");
          if (row && row.dataset.tierId) {
            markRowDeleted(row);
          }
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
        } else if (btn.classList.contains("js-remove-sponsor")) {
          var spRow = btn.closest(".sponsor-row");
          if (spRow && spRow.dataset.sponsorId) {
            markRowDeleted(spRow);
          }
        } else if (btn.classList.contains("js-remove-row")) {
          removeNewRow(btn.closest("tr, .sponsor-row--new"));
        }
      });
    }

    // --- Organising committee ---
    var ocTmpl = document.getElementById("oc-row-tmpl");
    var ocTbody = document.getElementById("oc-tbody");
    var addOcBtn = document.getElementById("add-oc-btn");

    if (addOcBtn && ocTmpl && ocTbody) {
      addOcBtn.addEventListener("click", function () {
        var clone = ocTmpl.content.firstElementChild.cloneNode(true);
        ocTbody.appendChild(clone);
      });
    }

    if (ocTbody) {
      ocTbody.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        if (btn.classList.contains("js-remove-oc")) {
          var row = btn.closest("tr");
          if (row && row.dataset.ocId) {
            markRowDeleted(row);
          }
        } else if (btn.classList.contains("js-remove-row")) {
          removeNewRow(btn.closest("tr"));
        }
      });
    }

    // --- Sub-events ---
    var seTmpl = document.getElementById("se-row-tmpl");
    var seTbody = document.getElementById("se-tbody");
    var addSeBtn = document.getElementById("add-se-btn");

    if (addSeBtn && seTmpl && seTbody) {
      addSeBtn.addEventListener("click", function () {
        var clone = seTmpl.content.firstElementChild.cloneNode(true);
        seTbody.appendChild(clone);
      });
    }

    if (seTbody) {
      seTbody.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        if (btn.classList.contains("js-remove-se")) {
          var row = btn.closest("tr");
          if (row && row.dataset.seId) {
            markRowDeleted(row);
          }
        } else if (btn.classList.contains("js-remove-row")) {
          removeNewRow(btn.closest("tr"));
        }
      });
    }
  });

  // --- OC Modal overlay (runs immediately) ---
  var ocModal = document.getElementById("oc-modal-overlay");
  var ocTrigger = document.getElementById("oc-trigger-btn");
  var ocClose = document.getElementById("oc-modal-close");

  function openOcModal() { if (ocModal) { ocModal.style.display = "flex"; document.body.style.overflow = "hidden"; } }
  function closeOcModal() { if (ocModal) { ocModal.style.display = "none"; document.body.style.overflow = ""; } }

  if (ocTrigger) ocTrigger.addEventListener("click", openOcModal);
  if (ocClose) ocClose.addEventListener("click", closeOcModal);
  if (ocModal) {
    ocModal.addEventListener("click", function (e) {
      if (e.target === ocModal) closeOcModal();
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && ocModal && ocModal.style.display === "flex") {
      closeOcModal();
    }
  });
})();
