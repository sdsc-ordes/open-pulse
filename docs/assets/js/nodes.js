(function () {
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function hostFromUrl(url) {
    try { return new URL(url).host; } catch (_) { return url; }
  }

  function renderCard(node) {
    const flag = node.flag || "🌐";
    const status = String(node.status || "live").toLowerCase();
    const isLive = status === "live";
    const tag = status.replace(/-/g, " ");
    return [
      '<a class="node-card node-card--' + escapeHtml(status) + '" ',
      'href="' + escapeHtml(node.url) + '" ',
      'target="_blank" rel="noopener noreferrer" ',
      'aria-label="Open ' + escapeHtml(node.name) + ' — ' + escapeHtml(node.url) + '">',
        '<span class="node-card__flag" aria-hidden="true">' + escapeHtml(flag) + '</span>',
        '<span class="node-card__body">',
          '<span class="node-card__heading">',
            '<span class="node-card__name">' + escapeHtml(node.name) + '</span>',
            '<span class="node-card__status">' + escapeHtml(tag) + '</span>',
          '</span>',
          '<span class="node-card__institution">' + escapeHtml(node.institution || "") + '</span>',
          node.location ? '<span class="node-card__location">' + escapeHtml(node.location) + '</span>' : '',
          node.description ? '<span class="node-card__desc">' + escapeHtml(node.description) + '</span>' : '',
          '<span class="node-card__url"><code>' + escapeHtml(hostFromUrl(node.url)) + '</code></span>',
        '</span>',
        '<span class="node-card__arrow" aria-hidden="true">' + (isLive ? "→" : "·") + '</span>',
      '</a>'
    ].join("");
  }

  async function loadNodes() {
    try {
      const resp = await fetch("./data/nodes.json", { cache: "no-cache" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return await resp.json();
    } catch (e) {
      console.error("Could not load nodes.json", e);
      return null;
    }
  }

  function render(nodes) {
    const grid = document.getElementById("hosted-nodes-grid");
    if (!grid) return;
    grid.removeAttribute("data-loading");
    if (!nodes || !nodes.length) {
      grid.innerHTML = '<p class="nodes-grid__placeholder">No hosted instances listed yet.</p>';
      return;
    }
    grid.innerHTML = nodes.map(renderCard).join("");
  }

  document.addEventListener("DOMContentLoaded", async function () {
    render(await loadNodes());
  });
})();
