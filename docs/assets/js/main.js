(function () {
  const CLONE_COMMAND = "git clone https://github.com/sdsc-ordes/open-pulse.git";

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

  document.addEventListener("DOMContentLoaded", function () {
    const copyButton = document.getElementById("clone-copy-btn");
    if (!copyButton) {
      return;
    }

    copyButton.addEventListener("click", async function () {
      let copied = false;
      try {
        copied = await copyText(CLONE_COMMAND);
      } catch (_error) {
        copied = false;
      }
      flashCopyState(copyButton, copied);
    });
  });
})();
