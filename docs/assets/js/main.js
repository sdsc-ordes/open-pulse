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

    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.setAttribute("readonly", "");
    textArea.style.position = "absolute";
    textArea.style.left = "-9999px";
    document.body.appendChild(textArea);
    textArea.select();
    textArea.setSelectionRange(0, text.length);

    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (_error) {
      copied = false;
    }

    document.body.removeChild(textArea);
    return copied;
  }

  function flashCopyState(button, ok) {
    button.classList.remove("is-copied", "is-failed");
    void button.offsetWidth;
    button.classList.add(ok ? "is-copied" : "is-failed");
    button.textContent = ok ? "Copied" : "Copy failed";

    window.setTimeout(function () {
      button.classList.remove("is-copied", "is-failed");
      button.textContent = "Copy";
    }, 1250);
  }

  function selectTab(target) {
    const codeEl = document.getElementById("clone-command");
    if (!codeEl) return;
    const cmd = COMMANDS[target];
    if (typeof cmd !== "string") return;
    codeEl.textContent = cmd;
    codeEl.dataset.current = target;
    document.querySelectorAll(".install-tab").forEach(function (tab) {
      const active = tab.dataset.target === target;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".install-tab").forEach(function (tab) {
      tab.addEventListener("click", function () {
        selectTab(tab.dataset.target);
      });
    });

    const copyButton = document.getElementById("clone-copy-btn");
    const codeEl = document.getElementById("clone-command");
    if (!copyButton || !codeEl) return;

    copyButton.addEventListener("click", async function () {
      let copied = false;
      try {
        copied = await copyText(codeEl.textContent);
      } catch (_error) {
        copied = false;
      }
      flashCopyState(copyButton, copied);
    });
  });
})();
