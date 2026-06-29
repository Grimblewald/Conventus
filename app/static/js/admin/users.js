(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var rolePicker = document.getElementById("role_picker");
    if (rolePicker) {
      rolePicker.addEventListener("change", function () {
        var url = new URL(window.location.href);
        url.searchParams.set("role", this.value);
        window.location.href = url.toString();
      });
    }

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
  });
})();
