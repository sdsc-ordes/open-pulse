(function () {
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function hostFromUrl(url) {
    try { return new URL(url).host; } catch (_) { return url; }
  }

  async function loadNodes() {
    try {
      const resp = await fetch("./data/nodes.json", { cache: "no-cache" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return await resp.json();
    } catch (e) {
      console.error("Could not load nodes.json", e);
      return [];
    }
  }

  function renderLeafTab(node, idx) {
    const slug = String(node._source || node.name || ("node-" + idx))
      .replace(/\.ya?ml$/i, "")
      .toLowerCase();
    const name = node.name || slug;
    let mainHtml;
    if (node.logo) {
      // Logo already carries the brand name — render it as the only visible
      // mark and keep the textual label visually-hidden for a11y / search.
      mainHtml =
        '<img class="leaf-tab__logo" src="' + escapeHtml(node.logo) + '" alt="' + escapeHtml(name) + '">' +
        '<span class="leaf-tab__label visually-hidden">' + escapeHtml(name) + '</span>';
    } else {
      mainHtml =
        '<span class="leaf-tab__icon" aria-hidden="true">' + escapeHtml(node.flag || "🌐") + '</span>' +
        '<span class="leaf-tab__label">' + escapeHtml(name) + '</span>';
    }
    return [
      '<li>',
        '<button class="leaf-tab' + (idx === 0 ? ' is-active' : '') + '" ',
          'data-node-slug="' + escapeHtml(slug) + '" ',
          'data-node-index="' + idx + '" ',
          'type="button" role="tab" ',
          'aria-selected="' + (idx === 0 ? "true" : "false") + '">',
          mainHtml,
        '</button>',
      '</li>'
    ].join("");
  }

  function renderAddLeaf() {
    return [
      '<li>',
        '<a class="leaf-tab leaf-tab--add" href="./docs/operations/register-a-node/" ',
           'aria-label="Add your own node">',
          '<span class="leaf-tab__icon" aria-hidden="true">+</span>',
          '<span class="leaf-tab__label">Add yours</span>',
          '<span aria-hidden="true">→</span>',
        '</a>',
      '</li>'
    ].join("");
  }

  function renderDetail(node) {
    if (!node) {
      return '<p class="installer__placeholder">Select a node from the left.</p>';
    }
    const status = String(node.status || "live").toLowerCase();
    let headHtml;
    if (node.logo) {
      headHtml =
        '<img class="node-detail__logo" src="' + escapeHtml(node.logo) + '" alt="' + escapeHtml(node.name || "") + '">' +
        '<h3 class="node-detail__name visually-hidden">' + escapeHtml(node.name || "") + '</h3>';
    } else {
      headHtml =
        '<span class="node-detail__flag" aria-hidden="true">' + escapeHtml(node.flag || "🌐") + '</span>' +
        '<h3 class="node-detail__name">' + escapeHtml(node.name || "") + '</h3>';
    }
    return [
      '<div class="node-detail node-detail--' + escapeHtml(status) + '">',
        '<div class="node-detail__head">',
          headHtml,
          '<span class="node-detail__status">' + escapeHtml(status.replace(/-/g, " ")) + '</span>',
        '</div>',
        node.institution ? '<p class="node-detail__meta">' + escapeHtml(node.institution) + (node.location ? ' · ' + escapeHtml(node.location) : '') + '</p>' : '',
        node.description ? '<p class="node-detail__desc">' + escapeHtml(node.description) + '</p>' : '',
        '<a class="node-detail__cta" href="' + escapeHtml(node.url) + '" target="_blank" rel="noopener noreferrer">',
          '<span>Open ' + escapeHtml(hostFromUrl(node.url)) + '</span>',
          '<span class="node-detail__cta-arrow" aria-hidden="true">→</span>',
        '</a>',
      '</div>'
    ].join("");
  }

  function activateLeaf(idx, nodes) {
    document
      .querySelectorAll('.installer__panel[data-super="nodes"] .leaf-tab:not(.leaf-tab--add)')
      .forEach(function (tab) {
        const isThis = Number(tab.dataset.nodeIndex) === idx;
        tab.classList.toggle("is-active", isThis);
        tab.setAttribute("aria-selected", isThis ? "true" : "false");
      });
    const body = document.getElementById("nodes-body");
    if (body) body.innerHTML = renderDetail(nodes[idx]);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    const list = document.getElementById("nodes-leaf-tabs");
    const body = document.getElementById("nodes-body");
    if (!list || !body) return;

    const nodes = await loadNodes();

    if (!nodes.length) {
      list.innerHTML = renderAddLeaf();
      body.innerHTML = '<p class="installer__placeholder">No nodes yet — be the first.</p>';
      return;
    }

    list.innerHTML = nodes.map(renderLeafTab).join("") + renderAddLeaf();
    activateLeaf(0, nodes);

    list.addEventListener("click", function (event) {
      const tab = event.target.closest(".leaf-tab:not(.leaf-tab--add)");
      if (!tab) return;
      const idx = Number(tab.dataset.nodeIndex);
      if (Number.isFinite(idx)) activateLeaf(idx, nodes);
    });
  });
})();
