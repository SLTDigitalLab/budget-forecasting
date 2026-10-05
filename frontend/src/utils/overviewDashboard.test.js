import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  actualYearSeries,
  forecastViewFromRun,
  latestCompleteActualYear,
  selectNewestForecast,
  shareDistribution,
  STORED_FORECAST_STATUS,
} from "./overviewDashboard.js";

const root = dirname(fileURLToPath(import.meta.url));
const overviewSource = readFileSync(resolve(root, "../pages/Overview.jsx"), "utf8");
const dashboardSource = readFileSync(resolve(root, "./overviewDashboard.js"), "utf8");
const cssSource = readFileSync(resolve(root, "../styles/global.css"), "utf8");

function yearMonths(year, months, amount = 10) {
  return months.map((month) => ({
    month: `${year}-${String(month).padStart(2, "0")}`,
    actual_amount: amount,
  }));
}

const FULL_YEAR = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

test("Jan-Dec 2025 plus a partial 2026 keeps 2025 as the latest complete year", () => {
  const rows = [...yearMonths(2025, FULL_YEAR, 100), ...yearMonths(2026, [1, 2, 3, 4, 5, 6], 80)];
  assert.equal(latestCompleteActualYear(rows), 2025);
});

test("a later complete year replaces the previous complete year", () => {
  const rows = [...yearMonths(2025, FULL_YEAR), ...yearMonths(2026, FULL_YEAR)];
  assert.equal(latestCompleteActualYear(rows), 2026);
});

test("a year missing one month does not qualify", () => {
  const rows = [...yearMonths(2025, FULL_YEAR), ...yearMonths(2026, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])];
  assert.equal(latestCompleteActualYear(rows), 2025);
  assert.equal(latestCompleteActualYear(yearMonths(2026, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])), null);
});

test("blank months are not filled and forecast rows are not treated as actuals", () => {
  const rows = [
    ...yearMonths(2024, FULL_YEAR),
    ...yearMonths(2025, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]),
    { month: "2025-12", actual_amount: null },
  ];
  assert.equal(latestCompleteActualYear(rows), 2024);
  assert.equal(latestCompleteActualYear([]), null);
});

test("historical line uses only the detected complete year and leaves gaps null", () => {
  const rows = [...yearMonths(2025, FULL_YEAR, 5), ...yearMonths(2026, [1, 2, 3, 4, 5, 6], 9)];
  const series = actualYearSeries(rows, latestCompleteActualYear(rows));
  assert.equal(series.length, 12);
  assert.equal(series[0].label, "Jan");
  assert.equal(series[11].label, "Dec");
  assert.equal(series[0].year, 2025);
  assert.equal(series[0].actual_amount, 5);
  assert.equal(series[0].forecast_amount, undefined);
  const partial = actualYearSeries([{ month: "2025-01", actual_amount: null }, ...yearMonths(2024, FULL_YEAR, 3)], 2024);
  assert.equal(partial[0].actual_amount, 3);
  assert.equal(actualYearSeries([{ month: "2025-03", actual_amount: 8 }], 2025)[0].actual_amount, null);
});

test("historical Budget Code share uses the same year amounts and skips blanks", () => {
  const rows = [
    { budget_code: "B", actual_amount: 25 },
    { budget_code: "A", actual_amount: 75 },
    { budget_code: "C", actual_amount: null },
  ];
  const shares = shareDistribution(rows, "actual_amount");
  assert.deepEqual(shares.map((row) => row.budget_code), ["A", "B"]);
  assert.equal(shares[0].percent, 0.75);
  assert.equal(shares[1].percent, 0.25);
});

test("latest forecast is the newest saved run and ignores the historical category", () => {
  const records = [
    { id: 70, category: "Int'l Settlement", generated_at: "2026-09-26T12:00:00Z", requested_start_month: "2026-07", requested_end_month: "2026-12" },
    { id: 72, category: "All Categories", generated_at: "2026-09-26T16:26:31Z", requested_start_month: "2027-01", requested_end_month: "2027-12" },
    { id: 90, category: "All Categories", generated_at: "2026-09-29T12:55:00Z", requested_start_month: "2027-01", requested_end_month: "2027-01" },
    { id: 91, category: "Travelling", generated_at: "2026-09-29T13:00:00Z", requested_start_month: "2027-01", requested_end_month: "2027-12" },
  ];
  const latest = selectNewestForecast(records);
  assert.equal(latest.id, 91);
  assert.equal(latest.category, "Travelling");
  assert.equal(selectNewestForecast([
    { id: 3, category: "Staff cost", generated_at: "2026-09-29T14:00:00Z" },
    { id: 91, category: "Travelling", generated_at: "2026-09-29T13:00:00Z" },
  ]).category, "Staff cost");
  assert.equal(selectNewestForecast([]), null);
  assert.equal(selectNewestForecast([
    { id: 5, category: "All Categories", generated_at: "2026-09-01T00:00:00Z" },
    { id: 1, category: "Travelling", generated_at: "2026-09-29T13:00:00Z" },
  ]).id, 1);
});

test("forecast chart and Budget Code distribution use one run and only stored months", () => {
  const oneMonth = forecastViewFromRun({
    id: 90,
    category: "All Categories",
    overall_total: 8,
    monthly_forecasts: [{ month: "2027-01", forecast_amount: 8 }],
    budget_code_forecasts: [
      { budget_code: "A", category: "Staff cost", available: true, forecast: { "2027-01": 8 } },
      { budget_code: "B", category: "Travelling", available: false, forecast: { "2027-01": 99 } },
    ],
  });
  assert.equal(oneMonth.runId, 90);
  assert.equal(oneMonth.months.length, 1);
  assert.equal(oneMonth.months[0].month, "2027-01");
  assert.equal(oneMonth.codes[0].budget_code, "A");
  assert.equal(oneMonth.codes.length, 1);

  const travelling = {
    id: 91,
    category: "Travelling",
    overall_total: 30,
    monthly_forecasts: ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"].map((month, index) => ({
      month: `2027-${month}`,
      forecast_amount: index + 1,
    })),
    budget_code_forecasts: [
      { budget_code: "T1", category: "Travelling", available: true, forecast: { "2027-01": 1, "2027-02": 2 } },
      { budget_code: "T2", category: "Travelling", available: true, forecast: { "2027-01": 0, "2027-03": 4 } },
      { budget_code: "S1", category: "Staff cost", available: true, forecast: { "2027-01": 100 } },
    ],
  };
  const fullYear = forecastViewFromRun(travelling);
  assert.equal(fullYear.runId, 91);
  assert.equal(fullYear.category, "Travelling");
  assert.equal(fullYear.months.length, 12);
  assert.equal(fullYear.months[0].forecast_amount, 1);
  assert.equal(fullYear.months[11].month, "2027-12");
  assert.equal(fullYear.codes.length, 3);
  assert.equal(fullYear.codes.every((row) => row.runId == null), true);
  const gap = forecastViewFromRun({
    id: 91,
    category: "Travelling",
    overall_total: 3,
    monthly_forecasts: [
      { month: "2027-01", forecast_amount: 1 },
      { month: "2027-02", forecast_amount: null },
      { month: "2027-03", forecast_amount: 2 },
    ],
    budget_code_forecasts: [
      { budget_code: "T1", available: true, forecast: { "2027-01": 1, "2027-03": 2 } },
    ],
  });
  assert.deepEqual(gap.months.map((row) => row.month), ["2027-01", "2027-03"]);
  assert.equal(forecastViewFromRun({ id: 1, budget_code_forecasts: [], monthly_forecasts: [] }), null);
});

test("historical category refreshes actuals only and latest forecast stays on the newest run", () => {
  assert.match(overviewSource, /getOverviewCategories\(\)/);
  assert.match(overviewSource, /id="overview-category"/);
  assert.match(overviewSource, /categories\.map\(\(item\)/);
  assert.equal((overviewSource.match(/<select/g) || []).length, 1);
  assert.match(overviewSource, /getOverviewHistorical\(category\.id\)/);
  assert.match(overviewSource, /setCategoryId\(event\.target\.value\)/);
  assert.match(overviewSource, /selectNewestForecast\(historyRecords\)/);
  assert.match(overviewSource, /forecastViewFromRun/);
  assert.match(overviewSource, /\}, \[categoryId, categories\]\);/);
  assert.match(overviewSource, /\}, \[historyRecords, historyReady\]\);/);
  assert.doesNotMatch(overviewSource, /applicableForecastCandidates/);
  assert.doesNotMatch(overviewSource, /categoryForecastFromRun/);
  const historicalEffect = overviewSource.slice(
    overviewSource.indexOf("async function loadCategory"),
    overviewSource.indexOf("}, [categoryId, categories]);"),
  );
  assert.match(historicalEffect, /getOverviewHistorical/);
  assert.doesNotMatch(historicalEffect, /getForecastRecord/);
  assert.doesNotMatch(historicalEffect, /setForecastRun/);
});

test("historical and forecast charts are separate line charts for one category", () => {
  assert.match(overviewSource, /Monthly Actuals \(Latest Year: \$\{actualYear\}\) – \$\{categoryName\}/);
  assert.match(overviewSource, /Monthly Forecast \(\$\{forecastYear\}\) – \$\{forecastCategoryName\}/);
  assert.match(overviewSource, /<LineChart data=\{actualPoints\}/);
  assert.match(overviewSource, /<LineChart data=\{forecastPoints\}/);
  assert.match(overviewSource, /latest_complete_year_budget_codes/);
  assert.match(overviewSource, /Budget Code Distribution – \$\{categoryName\}/);
  assert.match(overviewSource, /Budget Code Distribution \(Latest Forecast\) – \$\{forecastCategoryName\}/);
  assert.match(overviewSource, /forecastViewFromRun/);
  assert.match(overviewSource, /selectNewestForecast/);
  assert.doesNotMatch(overviewSource, /ComposedChart/);
  assert.doesNotMatch(overviewSource, /vs Forecast/);
  assert.doesNotMatch(overviewSource, /Category-wise Forecast/);
  assert.doesNotMatch(overviewSource, /Latest Month Budget Share/);
  assert.doesNotMatch(overviewSource, /<h2[^>]*>Forecasting History/);
  const latestSection = overviewSource.slice(overviewSource.indexOf('aria-label="Latest Forecast"'));
  assert.doesNotMatch(latestSection, /<select/);
  assert.doesNotMatch(overviewSource, /Latest Forecasting/);
});

test("Recent Forecasting Activities shows at most five stored rows and links to history", () => {
  assert.match(overviewSource, /historyRecords\.slice\(0, 5\)/);
  assert.match(overviewSource, /View All History →/);
  assert.match(overviewSource, /to="\/forecast-history"/);
  assert.match(overviewSource, /STORED_FORECAST_STATUS/);
  assert.equal(STORED_FORECAST_STATUS, "Completed");
  assert.doesNotMatch(overviewSource, /Triggered By/);
});

test("empty states do not invent zero amounts", () => {
  assert.match(overviewSource, /No historical actual data is available for this category\./);
  assert.match(overviewSource, /No complete 12-month actual year is available for this category\./);
  assert.match(overviewSource, /No forecast is available yet\./);
  assert.match(overviewSource, /Budget Code breakdown is not available for this record\./);
  assert.match(overviewSource, /formatSharePercent\(forecastView\?\.share\)/);
  assert.match(dashboardSource, /share: overall == null \|\| overall === 0 \? null/);
  assert.doesNotMatch(overviewSource, /actual_amount: 0/);
  assert.doesNotMatch(overviewSource, /forecast_amount: 0/);
});

test("Overview keeps one page scroll and internal scroll only for Budget Code lists", () => {
  assert.match(overviewSource, /overview-forecast-code-list-scroll data-scroll/);
  assert.doesNotMatch(overviewSource, /overview-category-scroll data-scroll/);
  assert.doesNotMatch(overviewSource, /table-wrap data-scroll/);
  const pageRule = cssSource.match(/\.overview-page \{[^}]*\}/);
  assert.ok(pageRule);
  assert.match(pageRule[0], /overflow-x:\s*clip/);
  assert.doesNotMatch(pageRule[0], /overflow-y:\s*auto/);
  assert.match(cssSource, /\.content \{[\s\S]*?overflow-y:\s*auto/);
  assert.match(cssSource, /\.overview-split \{[\s\S]*65fr[\s\S]*35fr/);
});
