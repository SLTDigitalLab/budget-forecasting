import { mergeHistoricalActuals } from "./forecastComparison.js";
import { isoToLabel } from "./months.js";

function chronologicalForecastMonths(forecast) {
  return (Array.isArray(forecast?.monthly_forecasts) ? forecast.monthly_forecasts.slice() : [])
    .filter((row) => row && row.month)
    .sort((left, right) => String(left.month).localeCompare(String(right.month)));
}

function isIsoMonth(value) {
  return /^\d{4}-\d{2}$/.test(String(value || ""));
}

function mean(values) {
  const finite = (values || []).filter((value) => Number.isFinite(value));
  if (!finite.length) {
    return null;
  }
  return finite.reduce((sum, value) => sum + value, 0) / finite.length;
}

export function seriesDirection(values) {
  const finite = (values || []).filter((value) => Number.isFinite(value));
  if (finite.length < 2) {
    return { direction: "insufficient", percent: null, label: "Not enough months" };
  }
  const first = finite[0];
  const last = finite[finite.length - 1];
  if (first === 0) {
    if (last === 0) {
      return { direction: "stable", percent: 0, label: "Stable" };
    }
    return {
      direction: last > 0 ? "up" : "down",
      percent: null,
      label: last > 0 ? "Rising" : "Falling",
    };
  }
  const percent = ((last - first) / Math.abs(first)) * 100;
  if (Math.abs(percent) < 0.5) {
    return { direction: "stable", percent, label: "Stable" };
  }
  if (percent > 0) {
    return { direction: "up", percent, label: "Rising" };
  }
  return { direction: "down", percent, label: "Falling" };
}

export function buildBudgetTrend({ monthlyActuals, forecast }) {
  const actuals = mergeHistoricalActuals(monthlyActuals, forecast?.historical_actuals);
  const forecasts = chronologicalForecastMonths(forecast);
  const actualMonths = new Set(actuals.map((row) => row.month));

  const points = actuals.map((row) => ({
    month: row.month,
    label: isoToLabel(row.month),
    actual: Number(row.actual_amount),
    forecast: null,
    lower: null,
    upper: null,
    interval_status: null,
  }));

  forecasts.forEach((row) => {
    if (!isIsoMonth(row.month) || actualMonths.has(row.month)) {
      return;
    }
    if (!Number.isFinite(Number(row.forecast_amount))) {
      return;
    }
    const lower = Number(row.lower_bound);
    const upper = Number(row.upper_bound);
    points.push({
      month: row.month,
      label: isoToLabel(row.month),
      actual: null,
      forecast: Number(row.forecast_amount),
      lower: Number.isFinite(lower) ? lower : null,
      upper: Number.isFinite(upper) ? upper : null,
      interval_status: row.interval_status || null,
    });
  });

  points.sort((left, right) => String(left.month).localeCompare(String(right.month)));

  const historicalValues = actuals.map((row) => Number(row.actual_amount));
  const forecastValues = forecasts
    .filter(
      (row) =>
        isIsoMonth(row.month) &&
        !actualMonths.has(row.month) &&
        Number.isFinite(Number(row.forecast_amount))
    )
    .map((row) => Number(row.forecast_amount));

  const historicalAverage = mean(historicalValues);
  const forecastAverage = Number.isFinite(Number(forecast?.monthly_average))
    ? Number(forecast.monthly_average)
    : mean(forecastValues);
  const difference =
    historicalAverage == null || forecastAverage == null ? null : forecastAverage - historicalAverage;
  const differencePercent =
    difference == null || historicalAverage == null || historicalAverage === 0
      ? null
      : (difference / historicalAverage) * 100;

  return {
    points,
    historical_average: historicalAverage,
    forecast_average: forecastAverage,
    difference,
    difference_percent: differencePercent,
    historical_trend: seriesDirection(historicalValues),
    forecast_trend: seriesDirection(forecastValues),
    historical_month_count: historicalValues.length,
    forecast_month_count: forecastValues.length,
    has_expected_range: points.some(
      (row) => Number.isFinite(row.lower) && Number.isFinite(row.upper)
    ),
  };
}
