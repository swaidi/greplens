export type Theme = "system" | "light" | "dark";

let current: Theme = "system";
const media = window.matchMedia?.("(prefers-color-scheme: dark)");

function paint() {
  const dark = current === "dark" || (current === "system" && !!media?.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

media?.addEventListener?.("change", paint);

/** Apply Light, Dark, or follow the Windows setting ("system"). */
export function applyTheme(theme: Theme | undefined) {
  current = theme === "light" || theme === "dark" ? theme : "system";
  paint();
}
