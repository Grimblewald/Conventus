(function () {
  "use strict";

  document.querySelectorAll(".palette-input").forEach(function (el) {
    el.addEventListener("input", function () {
      var k = el.dataset.key.replace("palette_", "");
      var cssVar = {
        page_bg: "--c-page-bg", page_text: "--c-page-text", muted_text: "--c-muted-text",
        link: "--c-link", link_hover: "--c-link-hover",
        accent: "--c-accent", accent_ink: "--c-accent-ink",
        header_bg: "--c-header-bg", header_text: "--c-header-text",
        footer_bg: "--c-footer-bg", footer_text: "--c-footer-text",
        card_bg: "--c-card-bg", card_border: "--c-card-border",
        button_bg: "--c-button-bg", button_text: "--c-button-text"
      }[k];
      if (cssVar) {
        document.documentElement.style.setProperty(cssVar, el.value);
      }
      var codeEl = el.parentElement.querySelector("code");
      if (codeEl) {
        codeEl.textContent = el.value;
      }
    });
  });
})();
