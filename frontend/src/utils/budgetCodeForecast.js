import { MONTH_OPTIONS } from "./forecastPeriod.js";

export const TRAINED_BUDGET_CODE_COUNT = 12;
export const BUDGET_CODE_FORECAST_ERROR = "Unable to display Budget Code-wise forecast results.";
export const VIEW_BUDGET_CODE_FORECAST = "View Budget Code-wise Forecast";
export const HIDE_BUDGET_CODE_FORECAST = "Hide Budget Code-wise Forecast";
export const UNAVAILABLE_REASON = "Insufficient historical data for reliable model evaluation.";

const AMOUNT_TOLERANCE = 1e-6;

function isFiniteAmount(value) {
  return Number.isFinite(Number(value));
}

function optionalBound(value) {
  return isFiniteAmount(value) ? Number(value) : null;
}

export function fullMonthLabel(isoMonth) {
  const [year, month] = String(isoMonth || "").split("-");
  const found = MONTH_OPTIONS.find((item) => item.value === month);
  if (!found || !year) {
    return String(isoMonth || "").trim();
  }
  return `${found.label} ${year}`;
}

export function isSingleMonthForecast(forecast) {
  const start = String(forecast?.requested_start_month || "").trim();
  const end = String(forecast?.requested_end_month || "").trim();
  return Boolean(start) && start === end;
}

function selectedBudgetCodes(forecast) {
  return (Array.isArray(forecast?.selected_accounts) ? forecast.selected_accounts : [])
    .map((code) => String(code || "").trim())
    .filter((code) => code && code.toLowerCase() !== "other");
}

function amountsMatch(left, right) {
  return Math.abs(Number(left) - Number(right)) <= AMOUNT_TOLERANCE;
}

function monthAmountFromForecast(entry, month) {
  if (!entry || typeof entry !== "object") {
    return null;
  }
  if (entry.forecast && typeof entry.forecast === "object" && isFiniteAmount(entry.forecast[month])) {
    return Number(entry.forecast[month]);
  }
  if (isFiniteAmount(entry.forecast_amount) && String(entry.month || "").trim() === month) {
    return Number(entry.forecast_amount);
  }
  return null;
}

function buildFromBudgetCodeForecasts(forecast, month, monthLabel, title) {
  const failure = (message = BUDGET_CODE_FORECAST_ERROR) => ({
    error: message,
    month,
    monthLabel,
    title,
    rows: [],
    combined_total: null,
  });
  const selected = selectedBudgetCodes(forecast);
  const items = [];
  for (const entry of forecast.budget_code_forecasts) {
    const code = String(entry?.budget_code || "").trim();
    if (!code || code.toLowerCase() === "other") {
      continue;
    }
    if (selected.length && !selected.includes(code)) {
      continue;
    }
    const available = entry.available !== false && String(entry.display_status || "") !== "Forecast Unavailable";
    const amount = available ? monthAmountFromForecast(entry, month) : null;
    if (available && !isFiniteAmount(amount)) {
      return failure();
    }
    items.push({
      budget_code: code,
      account_name: String(entry.account_name || "").trim(),
      forecast_amount: available ? Number(amount) : null,
      lower_bound: null,
      upper_bound: null,
      algorithm: entry.algorithm || null,
      display_status: entry.display_status || (available ? "Available" : "Forecast Unavailable"),
      forecast_type: entry.forecast_type || null,
      available,
      reason: available ? "" : (entry.reason || UNAVAILABLE_REASON),
    });
  }
  if (!items.length) {
    return failure();
  }

  const availableItems = items.filter((row) => row.available);
  const combinedTotal = availableItems.reduce((sum, row) => sum + Number(row.forecast_amount), 0);
  if (!isFiniteAmount(combinedTotal)) {
    return failure();
  }
  const combinedMonth = (Array.isArray(forecast.monthly_forecasts) ? forecast.monthly_forecasts : []).find(
    (row) => String(row?.month || "").trim() === month && isFiniteAmount(row.forecast_amount)
  );
  if (!combinedMonth || !amountsMatch(combinedMonth.forecast_amount, combinedTotal)) {
    return failure();
  }
  if (!amountsMatch(forecast.overall_total, combinedTotal)) {
    return failure();
  }

  const ranked = [...availableItems].sort((left, right) => {
    const delta = right.forecast_amount - left.forecast_amount;
    return delta !== 0 ? delta : left.budget_code.localeCompare(right.budget_code);
  });
  const unavailable = items.filter((row) => !row.available);
  const ordered = [...ranked, ...unavailable];
  return {
    error: "",
    month,
    monthLabel,
    title,
    combined_total: combinedTotal,
    combined_lower_bound: optionalBound(combinedMonth.lower_bound),
    combined_upper_bound: optionalBound(combinedMonth.upper_bound),
    amount_column: `${monthLabel} Forecast Amount`,
    rows: ordered.map((row, index) => ({
      rank: row.available ? index + 1 : "—",
      ...row,
      contribution_percent: !row.available || combinedTotal === 0
        ? (row.available ? 0 : null)
        : (row.forecast_amount / combinedTotal) * 100,
    })),
  };
}

export function buildBudgetCodeMonthForecast(forecast) {
  if (!isSingleMonthForecast(forecast)) {
    return null;
  }
  const month = String(forecast.requested_start_month).trim();
  const monthLabel = fullMonthLabel(month);
  const title = `Budget Code-wise Forecast — ${monthLabel}`;
  const failure = (message = BUDGET_CODE_FORECAST_ERROR) => ({
    error: message,
    month,
    monthLabel,
    title,
    rows: [],
    combined_total: null,
  });

  if (Array.isArray(forecast.budget_code_forecasts) && forecast.budget_code_forecasts.length) {
    return buildFromBudgetCodeForecasts(forecast, month, monthLabel, title);
  }

  const selected = selectedBudgetCodes(forecast);
  if (!selected.length || new Set(selected).size !== selected.length) {
    return failure();
  }

  const byCode = new Map();
  for (const row of Array.isArray(forecast.account_monthly_forecasts) ? forecast.account_monthly_forecasts : []) {
    const code = String(row?.budget_code || "").trim();
    const rowMonth = String(row?.month || "").trim();
    if (!code || rowMonth !== month || code.toLowerCase() === "other") {
      continue;
    }
    if (!selected.includes(code)) {
      continue;
    }
    if (byCode.has(code) || !isFiniteAmount(row.forecast_amount)) {
      return failure();
    }
    byCode.set(code, row);
  }

  const items = [];
  for (const code of selected) {
    const row = byCode.get(code);
    if (!row) {
      return failure();
    }
    items.push({
      budget_code: code,
      account_name: String(row.account_name || "").trim(),
      forecast_amount: Number(row.forecast_amount),
      lower_bound: optionalBound(row.lower_bound),
      upper_bound: optionalBound(row.upper_bound),
      algorithm: row.algorithm || null,
      display_status: "Available",
      forecast_type: null,
      available: true,
      reason: "",
    });
  }

  items.sort((left, right) => {
    const delta = right.forecast_amount - left.forecast_amount;
    return delta !== 0 ? delta : left.budget_code.localeCompare(right.budget_code);
  });

  const combinedTotal = items.reduce((sum, row) => sum + row.forecast_amount, 0);
  if (!isFiniteAmount(combinedTotal)) {
    return failure();
  }

  const combinedMonth = (Array.isArray(forecast.monthly_forecasts) ? forecast.monthly_forecasts : []).find(
    (row) => String(row?.month || "").trim() === month && isFiniteAmount(row.forecast_amount)
  );
  if (!combinedMonth || !amountsMatch(combinedMonth.forecast_amount, combinedTotal)) {
    return failure();
  }
  if (!amountsMatch(forecast.overall_total, combinedTotal)) {
    return failure();
  }

  return {
    error: "",
    month,
    monthLabel,
    title,
    combined_total: combinedTotal,
    combined_lower_bound: optionalBound(combinedMonth.lower_bound),
    combined_upper_bound: optionalBound(combinedMonth.upper_bound),
    amount_column: `${monthLabel} Forecast Amount`,
    rows: items.map((row, index) => ({
      rank: index + 1,
      ...row,
      contribution_percent: combinedTotal === 0 ? 0 : (row.forecast_amount / combinedTotal) * 100,
    })),
  };
}

export function budgetCodeForecastDisclosure({ forecast, expanded = false }) {
  const available = isSingleMonthForecast(forecast);
  const isOpen = Boolean(available && expanded);
  return {
    showAction: available,
    showTable: isOpen,
    actionLabel: isOpen ? HIDE_BUDGET_CODE_FORECAST : VIEW_BUDGET_CODE_FORECAST,
    usesExistingResponse: true,
  };
}
