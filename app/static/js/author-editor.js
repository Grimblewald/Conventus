(function () {
  "use strict";

  // The author and affiliation editor (templates/_author_editor.html).
  //
  // Authors refer to affiliations by an id that never changes, not by their
  // position, so removing an affiliation can never move someone's tick onto a
  // different one. Numbers are only worked out when the list is posted.
  // Typed text reaches the page through .value and .textContent only.

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-author-editor]").forEach(init);
  });

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function init(root) {
    var affilList = root.querySelector("[data-affil-list]");
    var authorList = root.querySelector("[data-author-list]");
    var output = root.querySelector("[data-author-output]");
    var radioName = "presenting-" + Math.random().toString(36).slice(2);

    var initial = {};
    try {
      initial = JSON.parse(root.querySelector("[data-author-initial]").textContent) || {};
    } catch (e) { /* a malformed payload starts the editor empty */ }

    var nextId = 1;
    var affils = (initial.affiliations || []).map(function (t) {
      return {id: nextId++, text: String(t || "")};
    });
    var authors = (initial.authors || []).map(function (a) {
      var ids = (a.affiliations || []).map(function (n) {
        return affils[n - 1] ? affils[n - 1].id : null;
      }).filter(Boolean);
      return {id: nextId++, name: String(a.name || ""), affils: ids};
    });
    if (!affils.length) affils.push({id: nextId++, text: ""});
    if (!authors.length) authors.push({id: nextId++, name: "", affils: []});
    var presenting = authors[initial.presenting || 0] || authors[0];
    var presentingId = presenting.id;

    // Blank rows are left out rather than refused: an empty row is what the
    // "+ Add" buttons leave behind. Everything else is checked on the server.
    function serialize() {
      var kept = affils.filter(function (a) { return a.text.trim(); });
      var number = {};
      kept.forEach(function (a, i) { number[a.id] = i + 1; });
      var people = authors.filter(function (a) { return a.name.trim(); });
      var p = people.map(function (a) { return a.id; }).indexOf(presentingId);
      output.value = JSON.stringify({
        affiliations: kept.map(function (a) { return a.text.trim(); }),
        authors: people.map(function (a) {
          return {
            name: a.name.trim(),
            affiliations: a.affils.map(function (id) { return number[id]; })
              .filter(Boolean).sort(function (x, y) { return x - y; })
          };
        }),
        presenting: p < 0 ? 0 : p
      });
    }

    function renderAffils() {
      affilList.textContent = "";
      affils.forEach(function (a, i) {
        var li = el("li", {style: "display:flex; gap:8px; align-items:center; margin-bottom:6px;"});
        li.appendChild(el("span", {style: "min-width:24px; font-weight:bold;"}, (i + 1) + "."));
        var input = el("input", {"class": "input input-sm", style: "flex:1;",
                                 placeholder: "Institution, city, country",
                                 "aria-label": "Affiliation " + (i + 1)});
        input.value = a.text;
        input.addEventListener("input", function () {
          a.text = input.value;
          renderAuthors();
          serialize();
        });
        li.appendChild(input);
        var remove = el("button", {type: "button", "class": "btn btn-quiet btn-sm",
                                   title: "Remove affiliation " + (i + 1)}, "×");
        remove.addEventListener("click", function () {
          affils = affils.filter(function (x) { return x !== a; });
          authors.forEach(function (p) {
            p.affils = p.affils.filter(function (id) { return id !== a.id; });
          });
          render();
        });
        li.appendChild(remove);
        affilList.appendChild(li);
      });
    }

    function renderAuthors() {
      authorList.textContent = "";
      authors.forEach(function (p, i) {
        var row = el("div", {"class": "author-row",
                             style: "display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px;"});
        var name = el("input", {"class": "input input-sm", style: "flex:2; min-width:180px;",
                                placeholder: "Full name", "aria-label": "Author " + (i + 1) + " name"});
        name.value = p.name;
        name.addEventListener("input", function () { p.name = name.value; serialize(); });
        row.appendChild(name);

        var ticks = el("span", {style: "display:flex; gap:8px; flex-wrap:wrap;"});
        affils.forEach(function (a, j) {
          var label = el("label", {"class": "check-label", title: a.text || "(empty)"});
          var box = el("input", {type: "checkbox",
                                 "aria-label": "Author " + (i + 1) + " at affiliation " + (j + 1)});
          box.checked = p.affils.indexOf(a.id) !== -1;
          box.addEventListener("change", function () {
            p.affils = p.affils.filter(function (id) { return id !== a.id; });
            if (box.checked) p.affils.push(a.id);
            serialize();
          });
          label.appendChild(box);
          label.appendChild(document.createTextNode(" " + (j + 1)));
          ticks.appendChild(label);
        });
        row.appendChild(ticks);

        var pres = el("label", {"class": "check-label"});
        var radio = el("input", {type: "radio", name: radioName,
                                 "aria-label": "Author " + (i + 1) + " presents"});
        radio.checked = p.id === presentingId;
        radio.addEventListener("change", function () { presentingId = p.id; serialize(); });
        pres.appendChild(radio);
        pres.appendChild(document.createTextNode(" Presenting"));
        row.appendChild(pres);

        if (authors.length > 1) {
          var remove = el("button", {type: "button", "class": "btn btn-quiet btn-sm",
                                     title: "Remove author " + (i + 1)}, "×");
          remove.addEventListener("click", function () {
            authors = authors.filter(function (x) { return x !== p; });
            if (presentingId === p.id) presentingId = authors[0].id;
            render();
          });
          row.appendChild(remove);
        }
        authorList.appendChild(row);
      });
    }

    function render() {
      renderAffils();
      renderAuthors();
      serialize();
    }

    root.querySelector("[data-add-affil]").addEventListener("click", function () {
      affils.push({id: nextId++, text: ""});
      render();
      var inputs = affilList.querySelectorAll("input");
      inputs[inputs.length - 1].focus();
    });
    root.querySelector("[data-add-author]").addEventListener("click", function () {
      authors.push({id: nextId++, name: "", affils: []});
      render();
      var rows = authorList.querySelectorAll(".author-row");
      rows[rows.length - 1].querySelector("input").focus();
    });

    var form = root.closest("form");
    if (form) form.addEventListener("submit", serialize);
    render();
  }
})();
