(function () {
  const COMMANDS = {
    docker: "docker pull ghcr.io/sdsc-ordes/open-pulse:v1.0.0",
    pip: "pip install open-pulse-science",
    source: "git clone https://github.com/sdsc-ordes/open-pulse.git",
  };

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
    const codeEl = document.getElementById("clone-command");
    if (!codeEl) return;
    const cmd = COMMANDS[target];
    if (typeof cmd !== "string") return;
    codeEl.textContent = cmd;
    codeEl.dataset.current = target;
    document
      .querySelectorAll('.installer__panel[data-super="self-deploy"] .leaf-tab')
      .forEach(function (tab) {
        const active = tab.dataset.leaf === target;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
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

    const copyButton = document.getElementById("clone-copy-btn");
    const codeEl = document.getElementById("clone-command");
    if (copyButton && codeEl) {
      copyButton.addEventListener("click", async function () {
        let ok = false;
        try { ok = await copyText(codeEl.textContent); } catch (_) { ok = false; }
        flashCopyState(copyButton, ok);
      });
    }
  });
})();
