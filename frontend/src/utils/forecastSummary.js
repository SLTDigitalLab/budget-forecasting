import { buildBudgetTrend } from "./budgetTrend.js";
import { forecastDistributionByCode } from "./forecastDistribution.js";
import { chronologicalForecasts, periodLabel } from "./forecastDisplay.js";
import {
  changeVsHistory,
  historicalMonthlyBaseline,
  pointInsight,
  specificMonthHistoricalBaseline,
  specificMonthInsight,
} from "./forecastHistoryBaseline.js";
import { isoToLabel } from "./months.js";

function isFiniteAmount(value) {
  return Number.isFinite(Number(value));
}

function pickExtremeMonth(rows, compare) {
  return (rows || []).reduce((winner, row) => {
    if (!row?.month || !isFiniteAmount(row.forecast_amount)) {
      return winner;
    }
    if (!winner) {
      return row;
    }
    return compare(Number(row.forecast_amount), Number(winner.forecast_amount)) ? row : winner;
  }, null);
}

function trendSentence(trend) {
  const label = trend?.label;
  if (label === "Rising") {
    return "Predicted monthly amounts are rising across the selected period.";
  }
  if (label === "Falling") {
    return "Predicted monthly amounts are falling across the selected period.";
  }
  if (label === "Stable") {
    return "Predicted monthly amounts are stable across the selected period.";
  }
  return "";
}

export function buildForecastSummary(forecast) {
  if (!forecast) {
    return null;
  }
  const months = chronologicalForecasts(forecast).filter((row) => isFiniteAmount(row.forecast_amount));
  const trend = buildBudgetTrend({
    monthlyActuals: forecast.historical_actuals,
    forecast,
  });
  const codes = forecastDistributionByCode(forecast);
  const peak = pickExtremeMonth(months, (current, best) => current > best);
  const trough = pickExtremeMonth(months, (current, best) => current < best);
  const expectedRangeMonths = months.filter(
    (row) => isFiniteAmount(row.lower_bound) && isFiniteAmount(row.upper_bound)
  ).length;
  const insights = [];
  if (peak?.month) {
    insights.push(`The highest predicted month is ${isoToLabel(peak.month)}.`);
  }
  const baseline = historicalMonthlyBaseline(forecast);
  months.forEach((row) => {
    const change = changeVsHistory(row.forecast_amount, baseline.average);
    const line = pointInsight(row.month, change.amount, change.percent, forecast.amount_unit);
    if (line) {
      insights.push(line);
    }
    const specific = specificMonthHistoricalBaseline(forecast, row.month);
    const specificChange = changeVsHistory(row.forecast_amount, specific.average);
    const specificLine = specificMonthInsight(
      row.month,
      specific.count > 0 ? specificChange.amount : null,
      specificChange.percent,
      forecast.amount_unit
    );
    if (specificLine) {
      insights.push(specificLine);
    }
  });
  const direction = trendSentence(trend.forecast_trend);
  if (direction) {
    insights.push(direction);
  }
  if (codes[0]) {
    const share = `${(Number(codes[0].percent) * 100).toFixed(1)}%`;
    const name = codes[0].account_name ? ` ${codes[0].account_name}` : "";
    insights.push(`${codes[0].budget_code}${name} accounts for ${share} of the predicted total.`);
  }
  if (expectedRangeMonths > 0 && expectedRangeMonths < months.length) {
    insights.push(
      `An expected range is available for ${expectedRangeMonths} of ${months.length} predicted months.`
    );
  } else if (expectedRangeMonths === months.length && months.length) {
    insights.push("An expected range is available for every predicted month.");
  }

  return {
    period: periodLabel(forecast),
    month_count: months.length,
    overall_total: Number(forecast.overall_total),
    monthly_average: Number(forecast.monthly_average),
    minimum_monthly_forecast: Number(forecast.minimum_monthly_forecast),
    maximum_monthly_forecast: Number(forecast.maximum_monthly_forecast),
    peak_month: peak?.month || "",
    trough_month: trough?.month || "",
    historical_average: baseline.average ?? trend.historical_average,
    difference: trend.difference,
    difference_percent: trend.difference_percent,
    forecast_trend: trend.forecast_trend,
    top_code: codes[0] || null,
    code_count: codes.length,
    expected_range_months: expectedRangeMonths,
    insights,
  };
}
