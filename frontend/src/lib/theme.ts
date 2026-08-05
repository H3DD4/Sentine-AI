/**
 * Theme state — one source of truth, outside the React tree.
 *
 * It lives here rather than in a component's `useState` for a specific reason:
 * `AppShell` is rendered *by each route*, so it unmounts and remounts on every
 * navigation. Component-local theme state therefore reset to its initial value
 * whenever the analyst changed page — switch to light, click "Report Builder",
 * and you are back in dark. Same defect class as the chat transcript that used
 * to vanish on navigation.
 *
 * The store below is module-level, so remounting reads the value that is
 * already there. `localStorage` (not `sessionStorage`) is right for this one:
 * unlike a transcript, a display preference carries no engagement data, and an
 * analyst should not have to re-pick it every morning.
 */

export type Theme = "light" | "dark";

const STORAGE_KEY = "sentinel.theme";

/**
 * Applied by an inline, blocking script before first paint — see
 * THEME_INIT_SCRIPT. Without it the server-rendered HTML ships with no `.dark`
 * class, so the app paints light, then snaps to dark once React hydrates. That
 * flash is indistinguishable from a broken theme.
 */
export const THEME_INIT_SCRIPT = `(function(){try{
var t=localStorage.getItem('${STORAGE_KEY}');
if(t!=='light'&&t!=='dark'){t=window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';}
document.documentElement.classList.toggle('dark',t==='dark');
document.documentElement.style.colorScheme=t;
}catch(e){document.documentElement.classList.add('dark');}})();`;

function systemTheme(): Theme {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  // Dark-first product (security ops console), so the OS has to ask for light
  // explicitly rather than dark having to be requested.
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function readStored(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    // Private mode / storage disabled. Not fatal: the theme just stops being
    // remembered across reloads, which is better than an unusable page.
    return null;
  }
}

let current: Theme = "dark";
let initialised = false;
const listeners = new Set<() => void>();

/** Reflect state into the DOM. `color-scheme` fixes native widgets and scrollbars. */
function paint(theme: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

function ensureInit() {
  if (initialised || typeof window === "undefined") return;
  initialised = true;
  current = readStored() ?? systemTheme();
  paint(current);
}

export function getTheme(): Theme {
  ensureInit();
  return current;
}

export function setTheme(theme: Theme) {
  ensureInit();
  if (theme === current) return;
  current = theme;
  paint(theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* preference simply is not remembered; the UI still switches */
  }
  listeners.forEach((l) => l());
}

export function toggleTheme() {
  setTheme(getTheme() === "dark" ? "light" : "dark");
}

export function subscribeTheme(listener: () => void): () => void {
  ensureInit();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** SSR snapshot. Must match THEME_INIT_SCRIPT's fallback or hydration warns. */
export function getServerTheme(): Theme {
  return "dark";
}
