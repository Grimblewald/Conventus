(function () {
  "use strict";

  // Authors and affiliations are handled by js/author-editor.js.

  document.addEventListener("DOMContentLoaded", function () {

    // ====================================================================
    // Word counting
    // ====================================================================

    function countWords(text) {
      return (text || "").trim().split(/\s+/).filter(Boolean).length;
    }

    var titleInput = document.getElementById("title");
    var titleWC = document.getElementById("title-word-count");
    var bodyInput = document.getElementById("body");
    var bodyWC = document.getElementById("body-word-count");

    function updateTitleCount() {
      if (!titleInput || !titleWC) return;
      var wc = countWords(titleInput.value);
      titleWC.textContent = "(" + wc + " word" + (wc !== 1 ? "s" : "") + ")";
      titleWC.style.color = wc > 15 ? "var(--c-accent)" : "";
    }

    function updateBodyCount() {
      if (!bodyInput || !bodyWC) return;
      var wc = countWords(bodyInput.value);
      bodyWC.textContent = "(" + wc + " / 300 word" + (wc !== 1 ? "s" : "") + ")";
      bodyWC.style.color = wc > 300 ? "var(--c-accent)" : "";
    }

    if (titleInput) titleInput.addEventListener("input", updateTitleCount);
    if (bodyInput) bodyInput.addEventListener("input", updateBodyCount);
    updateTitleCount();
    updateBodyCount();

    // ====================================================================
    // References
    // ====================================================================

    var refContainer = document.getElementById("reference-rows");
    var addRefBtn = document.getElementById("add-reference-btn");
    var refTmpl = document.getElementById("reference-row-tmpl");
    var refMsg = document.getElementById("reference-validation-msg");

    function parseBodyRefs() {
      if (!bodyInput) return [];
      var matches = bodyInput.value.match(/\[(\d+)\]/g) || [];
      var nums = [];
      matches.forEach(function (m) {
        var n = parseInt(m.replace(/[\[\]]/g, ""), 10);
        if (nums.indexOf(n) === -1) nums.push(n);
      });
      return nums.sort(function (a, b) { return a - b; });
    }

    function getRefDOIs() {
      var dois = [];
      if (!refContainer) return dois;
      refContainer.querySelectorAll(".reference-row").forEach(function (row) {
        var inp = row.querySelector("[data-ref-doi]");
        if (inp) dois.push(inp.value.trim());
      });
      return dois;
    }

    function reindexRefs() {
      if (!refContainer) return;
      refContainer.querySelectorAll(".reference-row").forEach(function (row, idx) {
        row.querySelector(".ref-key").textContent = "[" + (idx + 1) + "]";
      });
    }

    function validateReferences() {
      if (!refMsg) return;
      var bodyRefs = parseBodyRefs();
      var dois = getRefDOIs();
      var maxKey = dois.filter(function (d) { return d !== ""; }).length;
      var errors = [];

      bodyRefs.forEach(function (n) {
        if (n > maxKey) errors.push("Citation [​" + n + "​] in text has no matching reference.");
      });
      for (var k = 1; k <= maxKey; k++) {
        if (bodyRefs.indexOf(k) === -1) errors.push("Reference [​" + k + "​] is not cited in the abstract text.");
      }

      if (errors.length > 0) {
        refMsg.style.display = "";
        refMsg.style.color = "var(--c-accent)";
        refMsg.textContent = errors.join(" ");
      } else if (maxKey > 0) {
        refMsg.style.display = "";
        refMsg.style.color = "";
        refMsg.textContent = maxKey + " reference" + (maxKey !== 1 ? "s" : "") + " — all cited in text.";
      } else {
        refMsg.style.display = "none";
      }
    }

    function addRefRow() {
      if (!refContainer || !refTmpl) return;
      var clone = refTmpl.content.firstElementChild.cloneNode(true);
      refContainer.appendChild(clone);
      reindexRefs();
      var doiInput = clone.querySelector("[data-ref-doi]");
      var removeBtn = clone.querySelector(".js-remove-ref");
      if (removeBtn) {
        removeBtn.addEventListener("click", function () {
          clone.parentNode.removeChild(clone);
          reindexRefs();
          validateReferences();
        });
      }
      if (doiInput) {
        doiInput.addEventListener("input", validateReferences);
      }
    }

    if (addRefBtn) addRefBtn.addEventListener("click", addRefRow);
    if (bodyInput) bodyInput.addEventListener("input", validateReferences);

    // Restore the references of a draft being edited.
    //
    // This has to run here, once, and never again. It used to sit inside
    // serialize(), which had two consequences and both of them hurt. At the
    // point serialize() first ran, `refContainer` was still undefined — it is
    // assigned further down — so the guard was false and a draft's references
    // were never restored at all. And every later serialize() — one per
    // keystroke in an author name, plus one on submit — DID find it defined,
    // and rebuilt the rows from the stored draft, discarding whatever the
    // author had typed. An author who re-entered their DOIs and then touched
    // a co-author's name lost them again; the copy that reached the server had
    // none, so the body's [1] markers matched nothing and the submission was
    // refused for a reason the form itself had caused.
    //
    // Rows go through addRefRow() rather than being cloned inline, so a
    // restored row gets the same remove button and revalidation as one added
    // by hand — the old inline clone bound neither.
    var refsData = document.getElementById("references-data");
    if (refsData && refContainer && refTmpl) {
      try {
        var savedRefs = JSON.parse(refsData.textContent);
        if (Array.isArray(savedRefs) && savedRefs.length > 0) {
          refContainer.innerHTML = "";
          savedRefs.forEach(function (r) {
            addRefRow();
            var row = refContainer.lastElementChild;
            var doiInp = row && row.querySelector("[data-ref-doi]");
            if (doiInp) doiInp.value = r.doi || "";
          });
          reindexRefs();
          validateReferences();
        }
      } catch (e) { /* a malformed payload must not break the form */ }
    }
  });
})();
