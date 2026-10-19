export const OVERVIEW_PARTIAL_NOTE = "Historical overview is based on available Actuals data.";

export function compactOverviewError(message) {
  const text = String(message || "").trim();
  if (/Production Budget Codes are missing from the historical Actuals workbook/i.test(text)) {
    return OVERVIEW_PARTIAL_NOTE;
  }
  return text;
}

export function overviewHistoricalNote(payload) {
  const missing = Number(payload?.historical_missing_budget_code_count);
  if (payload?.historical_data_partial || (Number.isFinite(missing) && missing > 0)) {
    return OVERVIEW_PARTIAL_NOTE;
  }
  return "";
}
