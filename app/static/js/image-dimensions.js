// Warn about over-sized images before they are uploaded.
//
// The server has to enforce the limit regardless, but finding out after a
// slow upload of a 40 MB file — and being told about "size" when the problem
// is pixel count — sends people off compressing the file, which changes the
// wrong number. Checking here costs nothing and can say precisely what is
// wrong while the fix is still cheap.
//
// Applies to any file input carrying data-max-megapixels.
(function () {
  "use strict";

  function readDimensions(file, cb) {
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      cb(img.naturalWidth, img.naturalHeight);
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      cb(null, null);          // not an image we can measure; leave it to the server
    };
    img.src = url;
  }

  function hintFor(input) {
    var id = input.id + "-dimension-hint";
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div");
      el.id = id;
      el.className = "help";
      input.parentNode.insertBefore(el, input.nextSibling);
    }
    return el;
  }

  document.querySelectorAll("input[type=file][data-max-megapixels]").forEach(
    function (input) {
      var limit = parseFloat(input.getAttribute("data-max-megapixels"));
      if (!limit) return;

      input.addEventListener("change", function () {
        var hint = hintFor(input);
        input.setCustomValidity("");
        hint.textContent = "";
        hint.style.color = "";

        var file = input.files && input.files[0];
        if (!file) return;

        readDimensions(file, function (w, h) {
          if (!w || !h) return;
          var mp = (w * h) / 1000000;
          if (mp > limit) {
            var msg = "That image is " + w + " × " + h + " (" + mp.toFixed(0) +
              " megapixels). The limit is " + limit + " megapixels. This is " +
              "about the image's dimensions, not its file size — compressing " +
              "it will not help. Please resize it and choose it again.";
            hint.textContent = msg;
            hint.style.color = "var(--c-accent)";
            // Blocks submission natively, so no separate submit handler can
            // fall out of step with this check.
            input.setCustomValidity(msg);
          } else {
            hint.textContent = w + " × " + h + " (" + mp.toFixed(1) +
              " megapixels) — fine.";
          }
        });
      });
    });
})();
