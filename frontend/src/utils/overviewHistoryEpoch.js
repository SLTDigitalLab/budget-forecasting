const STORAGE_KEY = "overview-history-epoch";

/** Bump after confirmed dataset changes so Analytics/Overview refetch active history. */
export function bumpOverviewHistoryEpoch(store = globalThis.sessionStorage) {
  try {
    const value = String(Date.now());
    store?.setItem?.(STORAGE_KEY, value);
    return value;
  } catch {
    return "";
  }
}

export function readOverviewHistoryEpoch(store = globalThis.sessionStorage) {
  try {
    return String(store?.getItem?.(STORAGE_KEY) || "");
  } catch {
    return "";
  }
}
