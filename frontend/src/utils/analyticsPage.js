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

export const ANALYTICS_TABS = Object.freeze([
  { id: "historical", label: "Historical" },
  { id: "forecast", label: "Forecast" },
  { id: "comparison", label: "Comparison" },
]);

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

export function defaultAnalyticsTab(mode) {
  if (mode?.showHistorical || mode?.showHistoricalLoading || mode?.showHistoricalError || mode?.showHistoricalEmpty) {
    return "historical";
  }
  if (mode?.showForecastAnalysis || mode?.showBudgetTrend) {
    return "forecast";
  }
  if (mode?.showComparison) {
    return "comparison";
  }
  return "historical";
}

export function isAnalyticsTab(value) {
  return ANALYTICS_TABS.some((tab) => tab.id === value);
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
  const magnitude = rows.reduce((sum, row) => sum + Math.abs(Number(row.actual_amount)), 0);
  return rows.map((row, index) => ({
    ...row,
    rank: index + 1,
    // Signed share of total absolute magnitude so negatives stay signed and pie-unsafe zeros are avoided.
    share: magnitude === 0 ? 0 : Number(row.actual_amount) / magnitude,
  }));
}

function longHistoryLabel(isoMonth) {
  const [yearText, monthText] = String(isoMonth || "").split("-");
  const month = Number(monthText);
  if (!month || month < 1 || month > 12 || !yearText) {
    return isoMonth || "";
  }
  return `${MONTH_LABELS[month - 1]} ${yearText}`;
}

export function actualDataCoverageLabel(history) {
  const start = longHistoryLabel(history?.history_start);
  const end = longHistoryLabel(history?.history_end);
  return start && end ? `${start} – ${end}` : historyRangeLabel(history?.history_start, history?.history_end);
}

export function latestCompleteYearDisplay(history) {
  const year = history?.latest_complete_year;
  return year == null || year === "" ? "Unavailable" : String(year);
}

export function latestSubmissionMeta(history) {
  const month = history?.latest_available_month;
  if (!month) {
    return {
      monthLabel: "Unavailable",
      status: null,
      submitted: null,
      applicable: null,
      scopeLabel: history?.coverage_scope === "master" ? "All categories" : null,
    };
  }
  const submitted = Number(history.latest_available_submitted);
  const applicable = Number(history.latest_available_applicable);
  return {
    monthLabel: longHistoryLabel(month) || month,
    status: history.latest_available_status === "complete" ? "Complete" : "Partial",
    submitted: Number.isFinite(submitted) ? submitted : null,
    applicable: Number.isFinite(applicable) ? applicable : null,
    scopeLabel: history?.coverage_scope === "master" ? "All categories" : null,
  };
}

export function partialYearNote(yearlyActuals, history) {
  const partial = (Array.isArray(yearlyActuals) ? yearlyActuals : [])
    .filter((row) => row?.year_status === "PARTIAL_YEAR")
    .map((row) => row.year);
  if (!partial.length) {
    return "";
  }
  const compared = history?.latest_year_growth_compared_months
    || history?.yoy_compared_months
    || null;
  const compareText = Array.isArray(compared) && compared.length
    ? ` Compared months: ${compared.join(", ")}.`
    : "";
  return `Partial year · YoY compares matching months.${compareText}`;
}

/**
 * Restrict a saved forecast payload to codes belonging to the selected
 * historical category. Returns null when the forecast has no overlapping codes.
 */
export function forecastScopedToCategory(forecast, history) {
  if (!forecast) {
    return null;
  }
  const codes = new Set(
    (Array.isArray(history?.budget_codes) ? history.budget_codes : [])
      .map((row) => String(row?.budget_code || "").trim())
      .filter(Boolean)
  );
  if (!codes.size) {
    return null;
  }
  const monthly = (Array.isArray(forecast.account_monthly_forecasts) ? forecast.account_monthly_forecasts : [])
    .filter((row) => codes.has(String(row?.budget_code || "").trim()));
  if (!monthly.length) {
    return null;
  }
  const yearly = (Array.isArray(forecast.account_yearly_forecasts) ? forecast.account_yearly_forecasts : [])
    .filter((row) => codes.has(String(row?.budget_code || "").trim()));
  const monthTotals = new Map();
  monthly.forEach((row) => {
    const month = String(row.month || "");
    const amount = Number(row.forecast_amount);
    if (!month || !Number.isFinite(amount)) {
      return;
    }
    monthTotals.set(month, (monthTotals.get(month) || 0) + amount);
  });
  const combined = [...monthTotals.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([month, forecast_amount]) => ({ month, forecast_amount }));
  const overall = combined.reduce((sum, row) => sum + Number(row.forecast_amount), 0);
  return {
    ...forecast,
    category: history?.category?.name || forecast.category,
    account_monthly_forecasts: monthly,
    account_yearly_forecasts: yearly,
    monthly_forecasts: combined,
    overall_total: overall,
    monthly_average: combined.length ? overall / combined.length : null,
    selected_account_count: codes.size,
    forecast_month_count: combined.length,
  };
}
