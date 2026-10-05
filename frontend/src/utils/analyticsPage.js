const MONTH_LABELS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

export function analyticsPageMode({ historyStatus, history, forecast }) {
  return {
    showHistorical: Boolean(history) && historyStatus !== "empty",
    showHistoricalLoading: historyStatus === "loading" && !history,
    showHistoricalError: historyStatus === "error" && !history,
    showHistoricalEmpty: historyStatus === "empty" && !history,
    showForecastAnalysis: Boolean(forecast),
    showComparison: Boolean(forecast),
    showBudgetTrend: Boolean(forecast),
    blockPageOnMissingForecast: false,
  };
}

export function shortHistoryLabel(isoMonth) {
  const [yearText, monthText] = String(isoMonth || "").split("-");
  const month = Number(monthText);
  if (!month || month < 1 || month > 12) {
    return isoMonth || "";
  }
  return `${MONTH_LABELS[month - 1]}-${String(yearText).slice(-2)}`;
}

export function historyRangeLabel(startIso, endIso) {
  const start = shortHistoryLabel(startIso);
  const end = shortHistoryLabel(endIso);
  return start && end ? `${start} – ${end}` : "—";
}

export function validateHistoricalAnalysis(payload) {
  if (!payload?.category?.id || !payload?.category?.name) {
    return "Category is missing.";
  }
  if (!payload.history_start || !payload.history_end || payload.history_start > payload.history_end) {
    return "History period is invalid.";
  }
  const months = Array.isArray(payload.monthly_actuals) ? payload.monthly_actuals : [];
  if (!months.length) {
    return "Monthly actuals are missing.";
  }
  if (months.some((row) => !/^\d{4}-\d{2}$/.test(String(row.month || "")))) {
    return "Monthly actuals contain an invalid month.";
  }
  if (months.some((row) => row.actual_amount != null && !Number.isFinite(Number(row.actual_amount)))) {
    return "Monthly actuals contain a non-finite value.";
  }
  if (!Array.isArray(payload.yearly_actuals)) {
    return "Yearly actuals are missing.";
  }
  if (!Array.isArray(payload.seasonal_profile) || payload.seasonal_profile.length !== 12) {
    return "Seasonal profile is incomplete.";
  }
  const codes = Array.isArray(payload.budget_codes) ? payload.budget_codes : [];
  if (!codes.length) {
    return "Budget Code history is empty.";
  }
  if (new Set(codes.map((row) => String(row.budget_code))).size !== codes.length) {
    return "Budget Codes must be unique.";
  }
  if (codes.some((row) => !Number.isFinite(Number(row.actual_amount)))) {
    return "Budget Code actuals contain a non-finite value.";
  }
  return "";
}

export function codeHistoryRanking(codes) {
  const rows = (Array.isArray(codes) ? codes.slice() : [])
    .filter((row) => row?.budget_code && Number.isFinite(Number(row.actual_amount)))
    .sort((left, right) => {
      const delta = Number(right.actual_amount) - Number(left.actual_amount);
      return delta !== 0 ? delta : String(left.budget_code).localeCompare(String(right.budget_code));
    });
  const grand = rows.reduce((sum, row) => sum + Number(row.actual_amount), 0);
  return rows.map((row, index) => ({
    ...row,
    rank: index + 1,
    share: grand === 0 ? 0 : Number(row.actual_amount) / grand,
  }));
}
