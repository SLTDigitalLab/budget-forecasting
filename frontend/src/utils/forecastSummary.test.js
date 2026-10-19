import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { buildForecastSummary } from "./forecastSummary.js";

const here = dirname(fileURLToPath(import.meta.url));
const summarySource = readFileSync(resolve(here, "../components/ForecastSummary.jsx"), "utf8");
const resultsSource = readFileSync(resolve(here, "../components/ForecastResults.jsx"), "utf8");
const chartSource = readFileSync(resolve(here, "../components/MonthlyForecastChart.jsx"), "utf8");
const tableSource = readFileSync(resolve(here, "../components/ForecastTable.jsx"), "utf8");

const sample = {
  requested_start_month: "2026-07",
  requested_end_month: "2026-08",
  overall_total: 250,
  monthly_average: 125,
  minimum_monthly_forecast: 120,
  maximum_monthly_forecast: 130,
  historical_actuals: [
    { month: "2026-05", actual_amount: 100 },
    { month: "2026-06", actual_amount: 110 },
  ],
  monthly_forecasts: [
    { month: "2026-07", forecast_amount: 120, lower_bound: 110, upper_bound: 130 },
    { month: "2026-08", forecast_amount: 130, lower_bound: null, upper_bound: null },
  ],
  account_monthly_forecasts: [
    { budget_code: "A01", account_name: "First", month: "2026-07", forecast_amount: 80 },
    { budget_code: "A01", account_name: "First", month: "2026-08", forecast_amount: 80 },
    { budget_code: "A02", account_name: "Second", month: "2026-07", forecast_amount: 40 },
    { budget_code: "A02", account_name: "Second", month: "2026-08", forecast_amount: 50 },
  ],
};

test("forecast summary uses existing predicted totals without changing them", () => {
  const summary = buildForecastSummary(sample);
  assert.equal(summary.overall_total, 250);
  assert.equal(summary.monthly_average, 125);
  assert.equal(summary.minimum_monthly_forecast, 120);
  assert.equal(summary.maximum_monthly_forecast, 130);
  assert.equal(summary.period, "Jul-2026 to Aug-2026");
  assert.equal(summary.peak_month, "2026-08");
  assert.equal(summary.trough_month, "2026-07");
});

test("forecast summary reports expenditure growth against historical monthly average", () => {
  const summary = buildForecastSummary(sample);
  assert.equal(summary.historical_average, 105);
  assert.equal(summary.difference, 20);
  const pointLines = summary.insights.filter((line) => /versus the overall historical monthly average/.test(line));
  assert.equal(pointLines.length, 2);
  assert.ok(pointLines.some((line) => /Jul-2026/.test(line)));
  assert.ok(pointLines.some((line) => /Aug-2026/.test(line)));
  const specificLines = summary.insights.filter((line) => /Specific-month comparison unavailable/.test(line));
  assert.equal(specificLines.length, 2);
});

test("forecast summary highlights the largest Budget Code share", () => {
  const summary = buildForecastSummary(sample);
  assert.equal(summary.top_code.budget_code, "A01");
  assert.equal(summary.top_code.forecast_amount, 160);
  assert.ok(summary.insights.some((line) => /A01 First accounts for 64\.0% of the predicted total/.test(line)));
});

test("forecast summary insights do not name algorithms", () => {
  const summary = buildForecastSummary({
    ...sample,
    overall_best_algorithm: "SARIMA",
  });
  assert.equal(summary.insights.join(" ").includes("SARIMA"), false);
  assert.equal(summary.insights.join(" ").toLowerCase().includes("conformal"), false);
});

test("forecast summary is empty without a generated forecast", () => {
  assert.equal(buildForecastSummary(null), null);
});

test("generate forecast results no longer render key financial insights", () => {
  assert.doesNotMatch(summarySource, /Key financial insights/i);
  assert.doesNotMatch(summarySource, /forecast-summary-insights/);
  assert.doesNotMatch(resultsSource, /Key financial insights/i);
  assert.doesNotMatch(resultsSource, /forecast-summary-insights/);
  const summaryAt = resultsSource.indexOf("<ForecastSummary");
  const chartAt = resultsSource.indexOf("<MonthlyForecastChart");
  const tableAt = resultsSource.indexOf("<ForecastTable");
  const codesAt = resultsSource.indexOf("<BudgetCodeForecastTable");
  assert.ok(summaryAt > -1 && summaryAt < chartAt && chartAt < tableAt && tableAt < codesAt);
});

test("generate forecast chart and table still show both historical comparisons", () => {
  assert.match(chartSource, /Vs Overall Average/);
  assert.match(chartSource, /specificMonthAverageLabel/);
  assert.doesNotMatch(chartSource, /EXPECTED_RANGE_LABEL/);
  assert.match(resultsSource, /ExpectedRangeLabel/);
  assert.match(tableSource, /Change vs Overall History/);
  assert.match(tableSource, /Change vs Same Month History/);
  assert.match(tableSource, /historicalAverageCaption/);
  assert.match(tableSource, /formatBoundAmount/);
});
