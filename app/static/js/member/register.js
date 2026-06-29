(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("reg-form");
    if (!form) return;

    function evalCondition(field, cond) {
      var target = form.querySelector("[name=\"" + field + "\"]");
      if (!target) target = form.querySelector("[name=\"" + field + "[]\"]");
      if (!target) return cond.defaultVisible !== false;

      if (target.type === "radio") {
        var checked = form.querySelector("[name=\"" + field + "\"]:checked");
        if (!checked) return false;
        if (cond.value !== undefined) return checked.value === String(cond.value);
        if (cond.contains !== undefined) return checked.value.indexOf(cond.contains) !== -1;
        return true;
      }
      if (target.type === "checkbox") {
        if (cond.value !== undefined) return target.checked === (String(cond.value) === "1" || cond.value === true);
        if (cond.contains !== undefined) {
          var boxes = form.querySelectorAll("[name=\"" + field + "\"]:checked");
          for (var i = 0; i < boxes.length; i++) {
            if (boxes[i].value.indexOf(cond.contains) !== -1) return true;
          }
          return false;
        }
      }
      return true;
    }

    function refreshConditionalFields() {
      form.querySelectorAll("[data-cond-wrap]").forEach(function (wrap) {
        var field = wrap.getAttribute("data-cond-field");
        if (!field) return;
        var cond = {
          field: field,
          value: wrap.getAttribute("data-cond-value") || undefined,
          contains: wrap.getAttribute("data-cond-contains") || undefined,
          defaultVisible: false
        };
        wrap.style.display = evalCondition(field, cond) ? "" : "none";
      });
    }

    form.addEventListener("change", refreshConditionalFields);
    form.addEventListener("input", refreshConditionalFields);
    refreshConditionalFields();
  });
})();
