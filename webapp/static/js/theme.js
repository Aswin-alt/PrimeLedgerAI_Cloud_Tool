/**
 * Light / Dark theme toggle with localStorage persistence.
 * Applies data-theme on <html> before paint to avoid flash.
 * Values: "light" | "dark"
 * Backward-compat: old "day" maps to "light", "night" maps to "dark".
 */
(function () {
  var KEY = "pla_theme";

  function normalize(v) {
    if (v === "dark" || v === "night") return "dark";
    return "light";
  }

  function preferred() {
    try {
      var saved = localStorage.getItem(KEY);
      if (saved) return normalize(saved);
    } catch (e) {}
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return "dark";
    }
    return "light";
  }

  function apply(theme) {
    theme = normalize(theme);
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch (e) {}
    var evt = new CustomEvent("pla-theme", { detail: theme });
    document.dispatchEvent(evt);
    window.dispatchEvent(evt);
  }

  window.PLATheme = {
    get: function () {
      return normalize(document.documentElement.getAttribute("data-theme") || preferred());
    },
    set: apply,
    toggle: function () {
      apply(window.PLATheme.get() === "dark" ? "light" : "dark");
    },
  };

  apply(preferred());
})();
