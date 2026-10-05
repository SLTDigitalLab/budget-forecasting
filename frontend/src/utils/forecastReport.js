import { buildForecastComparison } from "./forecastComparison.js";
import { forecastDistributionByCode } from "./forecastDistribution.js";
import { chronologicalForecasts, periodLabel } from "./forecastDisplay.js";
import { buildForecastSummary } from "./forecastSummary.js";
import { isoToLabel } from "./months.js";

export const UNAVAILABLE = "Unavailable";

export const REPORT_LAYOUT = {
  pageOverflowX: "clip",
  tableOverflowX: "auto",
};

function monthFilePart(isoMonth) {
  const label = isoToLabel(isoMonth || "");
  const [month, year] = String(label).split("-");
  if (!month || !year) {
    return "Unknown";
  }
  return `${month}_${year}`;
}

export function reportFileStem(forecast) {
  const start = isoToLabel(forecast?.requested_start_month || "");
  const end = isoToLabel(forecast?.requested_end_month || "");
  return `SLT_Mobitel_Forecast_Report_${start}_to_${end}`;
}

export function excelFileStem(forecast) {
  return `AI_Budget_Forecast_${monthFilePart(forecast?.requested_start_month)}_${monthFilePart(forecast?.requested_end_month)}`;
}

export function exportPageState({ selectedId, previewStatus }) {
  const selected = Boolean(selectedId);
  const ready = previewStatus === "ready";
  return {
    showPreviewButton: selected,
    showDownloads: selected && ready,
    previewStartsDownload: false,
    emptyMessage: selected ? "" : "No completed forecast selected",
  };
}

export function isFiniteBound(value) {
  if (value == null || value === "") {
    return false;
  }
  return Number.isFinite(Number(value));
}

export function formatExpectedBound(value, formatAmount, amountUnit) {
  if (!isFiniteBound(value)) {
    return UNAVAILABLE;
  }
  return formatAmount(value, amountUnit);
}

export function monthOverMonthChange(current, previous) {
  if (!Number.isFinite(Number(current)) || !Number.isFinite(Number(previous))) {
    return null;
  }
  const prior = Number(previous);
  if (prior === 0) {
    return null;
  }
  return ((Number(current) - prior) / prior) * 100;
}

export function buildReportPreviewModel(forecast) {
  if (!forecast) {
    return null;
  }
  const summary = buildForecastSummary(forecast);
  const months = chronologicalForecasts(forecast);
  const selected = (Array.isArray(forecast.selected_accounts) ? forecast.selected_accounts : [])
    .map((code) => String(code || ""))
    .filter(Boolean);
  const codes = forecastDistributionByCode(forecast)
    .filter((row) => !selected.length || selected.includes(row.budget_code))
    .map((row, index) => ({ ...row, rank: index + 1 }));
  const monthly = months.map((row, index) => {
    const previous = index === 0 ? null : months[index - 1].forecast_amount;
    return {
      month: row.month,
      forecast_amount: row.forecast_amount,
      lower_bound: isFiniteBound(row.lower_bound) ? Number(row.lower_bound) : UNAVAILABLE,
      upper_bound: isFiniteBound(row.upper_bound) ? Number(row.upper_bound) : UNAVAILABLE,
      change_percent: monthOverMonthChange(row.forecast_amount, previous),
    };
  });
  const comparison = buildForecastComparison({
    historicalActuals: forecast.historical_actuals,
    forecast,
    historyEnd: forecast.history_end || forecast.historical_end,
  });
  return {
    title: "AI Budget Forecast Report",
    filename_stem: reportFileStem(forecast),
    excel_filename_stem: excelFileStem(forecast),
    triggersDownload: false,
    header: {
      period: summary?.period || periodLabel(forecast),
      generated_date: forecast.generated_at || "",
      category: forecast.category || "",
      forecast_month_count: Number(forecast.forecast_month_count || months.length),
      selected_account_count: Number(forecast.selected_account_count || selected.length),
    },
    summary,
    insights: summary?.insights || [],
    monthly_forecasts: monthly,
    budget_codes: codes,
    selected_accounts: selected,
    comparison,
    overall_total: Number(forecast.overall_total),
    monthly_average: Number(forecast.monthly_average),
    amount_unit: forecast.amount_unit || "LKR_MILLIONS",
    chart_months: months,
  };
}
