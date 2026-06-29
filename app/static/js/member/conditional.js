(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-cond-wrap]").forEach(function (wrap) {
      var form = wrap.closest("form");
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
          if (cond.value !== undefined) {
            var v = String(cond.value).toLowerCase();
            var isTrue = (v === "true" || v === "1" || cond.value === true);
            return target.checked === isTrue;
          }
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

      function refresh() {
        var field = wrap.getAttribute("data-cond-field");
        if (!field) return;
        var cond = {
          field: field,
          value: wrap.getAttribute("data-cond-value") || undefined,
          contains: wrap.getAttribute("data-cond-contains") || undefined,
          defaultVisible: false
        };
        var visible = evalCondition(field, cond);
        wrap.style.display = visible ? "" : "none";

        var isCondRequired = wrap.getAttribute("data-cond-required") === "true";
        if (isCondRequired) {
          var el = wrap.querySelector("input, select, textarea");
          if (el) {
            if (visible) {
              el.setAttribute("required", "");
            } else {
              el.removeAttribute("required");
            }
          }
        }
      }

      form.addEventListener("change", refresh);
      form.addEventListener("input", refresh);
      refresh();
    });
  });
})();
