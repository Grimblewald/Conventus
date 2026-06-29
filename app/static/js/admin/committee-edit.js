(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var dynRoleTmpl = document.getElementById("dyn-role-tmpl");
    var dynRoleContainer = document.getElementById("dyn-roles-container");
    var addDynRoleBtn = document.getElementById("add-dyn-role-btn");

    if (addDynRoleBtn && dynRoleTmpl && dynRoleContainer) {
      addDynRoleBtn.addEventListener("click", function () {
        var clone = dynRoleTmpl.content.firstElementChild.cloneNode(true);
        dynRoleContainer.appendChild(clone);
      });
    }

    if (dynRoleContainer) {
      dynRoleContainer.addEventListener("click", function (e) {
        var btn = e.target.closest(".js-rem-dyn-role");
        if (!btn) return;
        var row = btn.closest(".dyn-role-row");
        if (row && row.dataset.idx !== undefined) {
          var del = document.createElement("input");
          del.type = "hidden";
          del.name = "dyn_delete_" + row.dataset.idx;
          del.value = "1";
          row.appendChild(del);
          row.style.display = "none";
        } else if (row) {
          row.parentNode.removeChild(row);
        }
      });
    }
  });
})();
