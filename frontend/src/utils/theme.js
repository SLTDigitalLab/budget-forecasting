export const THEME_STORAGE_KEY = "ai-budget-theme";
export const THEME_LIGHT = "light";
export const THEME_DARK = "dark";

export function isThemeValue(value) {
  return value === THEME_LIGHT || value === THEME_DARK;
}

export function getSystemTheme(getMatches = defaultPrefersDark) {
  try {
    return getMatches() ? THEME_DARK : THEME_LIGHT;
  } catch {
    return THEME_LIGHT;
  }
}

function defaultPrefersDark() {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function readStoredTheme(storage = defaultStorage()) {
  try {
    const value = storage?.getItem?.(THEME_STORAGE_KEY);
    return isThemeValue(value) ? value : null;
  } catch {
    return null;
  }
}

export function writeStoredTheme(theme, storage = defaultStorage()) {
  if (!isThemeValue(theme)) {
    return false;
  }
  try {
    storage?.setItem?.(THEME_STORAGE_KEY, theme);
    return true;
  } catch {
    return false;
  }
}

export function resolveInitialTheme({
  storage = defaultStorage(),
  getMatches = defaultPrefersDark,
} = {}) {
  return readStoredTheme(storage) || getSystemTheme(getMatches);
}

export function applyTheme(theme, root = defaultRoot()) {
  const next = isThemeValue(theme) ? theme : THEME_LIGHT;
  if (!root) {
    return next;
  }
  root.dataset.theme = next;
  root.style.colorScheme = next;
  return next;
}

export function toggleTheme(theme) {
  return theme === THEME_DARK ? THEME_LIGHT : THEME_DARK;
}

export function themeActionLabel(theme) {
  return theme === THEME_DARK ? "Switch to light mode" : "Switch to dark mode";
}

function defaultStorage() {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

function defaultRoot() {
  return typeof document !== "undefined" ? document.documentElement : null;
}
