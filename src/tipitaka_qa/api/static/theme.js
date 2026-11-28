"use strict";
// Light / dark switch. A saved choice beats the system setting; with none, the page follows the system.
// Loaded in <head> without defer so the theme is set before the first paint (no flash of the wrong colours).
(() => {
  const root = document.documentElement;
  const system = matchMedia("(prefers-color-scheme: dark)");
  try {
    const t = localStorage.getItem("theme");
    if (t === "light" || t === "dark") root.dataset.theme = t;
  } catch { /* storage unavailable: follow the system */ }

  const current = () => root.dataset.theme || (system.matches ? "dark" : "light");
  const ICON = {   // the button shows where a press takes you
    dark: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
    light: '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"/></svg>',
  };
  function paint() {
    const next = current() === "dark" ? "light" : "dark";
    for (const b of document.querySelectorAll("[data-theme-toggle]")) {
      b.innerHTML = ICON[next];
      b.setAttribute("aria-label", next === "dark" ? "เปลี่ยนเป็นโหมดมืด" : "เปลี่ยนเป็นโหมดสว่าง");
      b.title = b.getAttribute("aria-label");
    }
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-theme-toggle]")) return;
    const t = current() === "dark" ? "light" : "dark";
    root.dataset.theme = t;
    try { localStorage.setItem("theme", t); } catch { /* the choice lasts for this page only */ }
    paint();
  });
  system.addEventListener?.("change", paint);
  document.addEventListener("DOMContentLoaded", paint);
})();
