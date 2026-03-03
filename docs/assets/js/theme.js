(function () {
  const STORAGE_KEY = "open-pulse-theme";
  const root = document.documentElement;

  function readStoredTheme() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (_error) {
      return null;
    }
  }

  function writeStoredTheme(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (_error) {
      return;
    }
  }

  function getTheme() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function updateToggle(theme) {
    const toggle = document.getElementById("theme-toggle");
    if (!toggle) {
      return;
    }

    const nextTheme = theme === "dark" ? "light" : "dark";
    const icon = toggle.querySelector(".theme-toggle__icon");

    toggle.setAttribute("aria-label", "Switch to " + nextTheme + " theme");
    toggle.setAttribute("aria-pressed", String(theme === "dark"));
    if (icon) {
      icon.textContent = theme === "dark" ? "☀︎" : "☾";
    }
  }

  function emitThemeChange(theme) {
    window.dispatchEvent(new CustomEvent("openpulse:themechange", { detail: { theme: theme } }));
  }

  function applyTheme(theme, persist) {
    root.setAttribute("data-theme", theme);
    if (persist) {
      writeStoredTheme(theme);
    }
    updateToggle(theme);
    emitThemeChange(theme);
  }

  function toggleTheme() {
    const nextTheme = getTheme() === "dark" ? "light" : "dark";
    applyTheme(nextTheme, true);
  }

  const initialTheme = readStoredTheme() || "dark";
  root.setAttribute("data-theme", initialTheme);

  window.openPulseTheme = {
    getTheme: getTheme,
    setTheme: function (theme) {
      applyTheme(theme === "light" ? "light" : "dark", true);
    },
    toggleTheme: toggleTheme
  };

  document.addEventListener("DOMContentLoaded", function () {
    updateToggle(getTheme());
    const toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", toggleTheme);
    }
  });

})();
