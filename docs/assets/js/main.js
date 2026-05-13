(function () {
  // Per-leaf content for the Self-deploy panel. Keep these short — for
  // the long story, link to /docs/getting-started.
  const SELF_DEPLOY = {
    docker: {
      icon: "🐳",
      title: "Docker (recommended)",
      summary:
        "Pull the prebuilt image and bring up the stack with compose. Fastest path if Docker is already running on the host.",
      prereq: "Docker + Docker Compose",
      command: "docker pull ghcr.io/sdsc-ordes/open-pulse:v1.0.0",
      followUp: "Then `./scripts/op deploy up --profile hub` to bring up the hub.",
      nextHref: "./docs/getting-started/",
      nextLabel: "Bring up the stack",
    },
    pip: {
      icon: "🐍",
      title: "pip — install the Python package",
      summary:
        "Install the Open Pulse CLI on your host. Best for embedding it in existing Python tooling or for development against an external stack.",
      prereq: "Python ≥ 3.11",
      command: "pip install 'open-pulse-science[hub]'",
      followUp: "Then `open-pulse --help` to see the available command groups.",
      nextHref: "./docs/getting-started/",
      nextLabel: "First commands",
    },
    source: {
      icon: "🛠",
      title: "Build from source",
      summary:
        "Clone the repo and build the image locally. Pick this for development, custom forks, or pre-release changes from the develop branch.",
      prereq: "git + Docker",
      command:
        "git clone https://github.com/sdsc-ordes/open-pulse && \\\n  cd open-pulse && \\\n  docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .",
      followUp: "Then `echo OPEN_PULSE_IMAGE=open-pulse:local >> infra/.env` and bring the stack up.",
      nextHref: "./docs/getting-started/",
      nextLabel: "Build & run",
    },
  };

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  // Render markdown-ish inline backticks → <code>
  function withInlineCode(s) {
    return escapeHtml(s).replace(/`([^`]+)`/g, function (_, c) {
      return '<code>' + c + '</code>';
    });
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
    document.body.removeChild(ta);
    return copied;
  }

  function flashCopyState(button, ok) {
    button.classList.remove("is-copied", "is-failed");
    void button.offsetWidth;
    button.classList.add(ok ? "is-copied" : "is-failed");
    button.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(function () {
      button.classList.remove("is-copied", "is-failed");
      button.textContent = "Copy";
    }, 1250);
  }

  function renderSelfDeploy(key) {
    const info = SELF_DEPLOY[key];
    const body = document.getElementById("self-deploy-body");
    if (!info || !body) return;
    body.innerHTML = [
      '<div class="leaf-detail leaf-detail--self-deploy" data-current="' + escapeHtml(key) + '">',
        '<div class="leaf-detail__heading">',
          '<span class="leaf-detail__icon" aria-hidden="true">' + escapeHtml(info.icon) + '</span>',
          '<h3 class="leaf-detail__title">' + escapeHtml(info.title) + '</h3>',
          info.prereq ? '<span class="leaf-detail__prereq" title="Prerequisites">Requires: ' + withInlineCode(info.prereq) + '</span>' : '',
        '</div>',
        '<p class="leaf-detail__summary">' + withInlineCode(info.summary) + '</p>',
        '<div class="leaf-detail__cmd">',
          '<pre><code id="clone-command" data-current="' + escapeHtml(key) + '">' + escapeHtml(info.command) + '</code></pre>',
          '<button id="clone-copy-btn" type="button" class="copy-btn" aria-label="Copy install command">Copy</button>',
        '</div>',
        info.followUp ? '<p class="leaf-detail__followup">' + withInlineCode(info.followUp) + '</p>' : '',
        info.nextHref ? '<a class="leaf-detail__next" href="' + escapeHtml(info.nextHref) + '">' + escapeHtml(info.nextLabel || "Next →") + ' <span aria-hidden="true">→</span></a>' : '',
      '</div>'
    ].join("");
    const copyButton = document.getElementById("clone-copy-btn");
    const codeEl = document.getElementById("clone-command");
    if (copyButton && codeEl) {
      copyButton.addEventListener("click", async function () {
        let ok = false;
        try { ok = await copyText(codeEl.textContent); } catch (_) { ok = false; }
        flashCopyState(copyButton, ok);
      });
    }
  }

  function switchSuper(target) {
    document.querySelectorAll(".super-tab").forEach(function (t) {
      const active = t.dataset.super === target;
      t.classList.toggle("is-active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".installer__panel").forEach(function (p) {
      const active = p.dataset.super === target;
      if (active) p.removeAttribute("hidden");
      else p.setAttribute("hidden", "");
    });
  }

  function selfDeploySelect(target) {
    if (!SELF_DEPLOY[target]) return;
    document
      .querySelectorAll('.installer__panel[data-super="self-deploy"] .leaf-tab')
      .forEach(function (tab) {
        const active = tab.dataset.leaf === target;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
    renderSelfDeploy(target);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".super-tab").forEach(function (tab) {
      tab.addEventListener("click", function () { switchSuper(tab.dataset.super); });
    });

    document
      .querySelectorAll('.installer__panel[data-super="self-deploy"] .leaf-tab')
      .forEach(function (tab) {
        tab.addEventListener("click", function () { selfDeploySelect(tab.dataset.leaf); });
      });

    // Initial render of the default self-deploy leaf (Docker).
    renderSelfDeploy("docker");
  });
})();
