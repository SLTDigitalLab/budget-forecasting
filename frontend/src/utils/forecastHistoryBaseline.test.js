import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  SPECIFIC_MONTH_UNAVAILABLE,
  calendarMonthName,
  calendarMonthShortName,
  changeVsHistory,
  formatChangeVsHistory,
  formatStackedChange,
  formatTooltipChange,
  historicalAverageCaption,
  historicalMonthlyBaseline,
  pointInsight,
  specificMonthAverageLabel,
  specificMonthChangeLabel,
  specificMonthHistoricalBaseline,
  specificMonthInsight,
} from "./forecastHistoryBaseline.js";
import { formatBoundAmount, isPresentAmount } from "./forecastDisplay.js";

const here = dirname(fileURLToPath(import.meta.url));
const tableSource = readFileSync(resolve(here, "../components/ForecastTable.jsx"), "utf8");
const chartSource = readFileSync(resolve(here, "../components/MonthlyForecastChart.jsx"), "utf8");
const resultsSource = readFileSync(resolve(here, "../components/ForecastResults.jsx"), "utf8");
const baselineSource = readFileSync(resolve(here, "./forecastHistoryBaseline.js"), "utf8");
const cssSource = readFileSync(resolve(here, "../styles/global.css"), "utf8");

const sampleForecast = {
  historical_monthly_average: 653.92,
  historical_month_count: 42,
  historical_actuals: [
    { month: "2023-08", actual_amount: 650 },
    { month: "2024-08", actual_amount: 670 },
    { month: "2025-08", actual_amount: 690.6 },
    { month: "2023-07", actual_amount: 600 },
    { month: "2024-07", actual_amount: 620 },
    { month: "2025-07", actual_amount: 640 },
    { month: "2023-01", actual_amount: 500 },
    { month: null, actual_amount: 0 },
    { month: "2024-08", actual_amount: null },
    { month: "2025-08", actual_amount: Number.NaN },
  ],
  monthly_forecasts: [
    { month: "2026-07", forecast_amount: 680 },
    {
      month: "2026-08",
      forecast_amount: 699.66,
      specific_month_historical_average: 670.2,
      specific_month_history_count: 3,
    },
    { month: "2026-09", forecast_amount: 633.82 },
    { month: "2027-01", forecast_amount: 510 },
  ],
};

test("every forecast point uses the identical overall historical average", () => {
  const baseline = historicalMonthlyBaseline(sampleForecast);
  assert.equal(baseline.average, 653.92);
  assert.equal(baseline.count, 42);
  const changes = sampleForecast.monthly_forecasts.map((row) => changeVsHistory(row.forecast_amount, baseline.average));
  assert.ok(changes.every((row) => Number.isFinite(row.amount)));
  assert.equal(new Set(sampleForecast.monthly_forecasts.map(() => baseline.average)).size, 1);
  assert.equal(changes[0].amount, 680 - 653.92);
  assert.equal(changes[1].amount, 699.66 - 653.92);
  assert.equal(changes[2].amount, 633.82 - 653.92);
});

test("every forecast point uses its matching calendar-month historical average", () => {
  const august = specificMonthHistoricalBaseline(sampleForecast, "2026-08");
  const july = specificMonthHistoricalBaseline(sampleForecast, "2026-07");
  const january = specificMonthHistoricalBaseline(sampleForecast, "2027-01");
  assert.equal(august.average, 670.2);
  assert.equal(august.count, 3);
  assert.equal(july.average, (600 + 620 + 640) / 3);
  assert.equal(july.count, 3);
  assert.equal(january.average, 500);
  assert.equal(january.count, 1);
  assert.notEqual(august.average, july.average);
  assert.equal(calendarMonthName("2026-08"), "August");
  assert.equal(calendarMonthShortName("2026-08"), "Aug");
  assert.equal(specificMonthAverageLabel("2026-08"), "Historical Aug Average");
});

test("forecast points are not compared with the preceding forecast point", () => {
  const baseline = historicalMonthlyBaseline(sampleForecast);
  const previousForecast = sampleForecast.monthly_forecasts[0].forecast_amount;
  const augustOverall = changeVsHistory(699.66, baseline.average);
  const augustSpecific = specificMonthHistoricalBaseline(sampleForecast, "2026-08");
  const monthOverMonth = 699.66 - previousForecast;
  assert.equal(augustOverall.amount, 699.66 - baseline.average);
  assert.notEqual(augustOverall.amount, monthOverMonth);
  assert.notEqual(augustSpecific.average, previousForecast);
  assert.notEqual(699.66 - augustSpecific.average, monthOverMonth);
  assert.doesNotMatch(tableSource, /Change from Previous Month/);
  assert.match(tableSource, /Change vs Overall History/);
  assert.match(tableSource, /Change vs Same Month History/);
  assert.doesNotMatch(tableSource, /previous = index === 0/);
  assert.doesNotMatch(resultsSource, /previousMonth|prevForecast|index - 1/);
});

test("forecast values are excluded from both historical averages", () => {
  const actualsOnly = {
    historical_actuals: [
      { month: "2023-08", actual_amount: 100 },
      { month: "2024-08", actual_amount: 200 },
      { month: "2025-08", actual_amount: 300 },
    ],
    monthly_forecasts: [{ month: "2026-08", forecast_amount: 9999 }],
  };
  const overall = historicalMonthlyBaseline(actualsOnly);
  const august = specificMonthHistoricalBaseline(actualsOnly, "2026-08");
  assert.equal(overall.average, 200);
  assert.equal(overall.count, 3);
  assert.equal(august.average, 200);
  assert.equal(august.count, 3);
  assert.doesNotMatch(baselineSource, /forecast_amount/);
});

test("positive, negative, and zero differences are calculated correctly", () => {
  const unit = "LKR_MILLIONS";
  const positive = changeVsHistory(699.66, 653.92);
  const negative = changeVsHistory(633.82, 653.92);
  const zero = changeVsHistory(653.92, 653.92);
  assert.equal(formatChangeVsHistory(positive.amount, (45.74 / 653.92) * 100, unit), "LKR +45.74 Mn (+6.99%)");
  assert.equal(formatChangeVsHistory(negative.amount, negative.percent, unit), "LKR \u221220.10 Mn (\u22123.07%)");
  assert.equal(formatChangeVsHistory(zero.amount, zero.percent, unit), "LKR 0.00 Mn (0.00%)");
  const stacked = formatStackedChange(positive.amount, (45.74 / 653.92) * 100, unit);
  assert.equal(stacked.amount, "LKR +45.74 Mn");
  assert.equal(stacked.percent, "+6.99%");
});

test("zero historical averages do not create Infinity or NaN", () => {
  const zeroAverage = changeVsHistory(10, 0);
  assert.equal(zeroAverage.amount, 10);
  assert.equal(zeroAverage.percent, null);
  assert.ok(Number.isFinite(zeroAverage.amount));
  assert.equal(zeroAverage.percent, null);
  assert.equal(formatChangeVsHistory(zeroAverage.amount, zeroAverage.percent, "LKR_MILLIONS"), "LKR +10.00 Mn");
  const bothZero = changeVsHistory(0, 0);
  assert.equal(bothZero.amount, 0);
  assert.equal(bothZero.percent, 0);
  assert.equal(formatChangeVsHistory(NaN, Infinity, "LKR_MILLIONS"), "—");
  const stacked = formatStackedChange(10, null, "LKR_MILLIONS");
  assert.equal(stacked.percent, "—");
});

test("missing specific-month history is handled clearly", () => {
  const missing = specificMonthHistoricalBaseline(
    { historical_actuals: [{ month: "2023-01", actual_amount: 100 }] },
    "2026-08"
  );
  assert.equal(missing.average, null);
  assert.equal(missing.count, 0);
  assert.equal(specificMonthInsight("2026-08", null, null, "LKR_MILLIONS"), `Aug-2026: ${SPECIFIC_MONTH_UNAVAILABLE}.`);
  assert.match(tableSource, /SPECIFIC_MONTH_UNAVAILABLE/);
  assert.match(chartSource, /Unavailable/);
});

test("history counts stay accurate including a single observation", () => {
  const fromActuals = historicalMonthlyBaseline({
    historical_actuals: [
      { month: "2023-01", actual_amount: 100 },
      { month: "2023-07", actual_amount: 300 },
      { month: "2024-01", actual_amount: 200 },
      { month: "2024-08", actual_amount: null },
      { month: "2025-08", actual_amount: Number.NaN },
    ],
  });
  assert.equal(fromActuals.count, 3);
  assert.equal(fromActuals.average, 200);
  const oneAugust = specificMonthHistoricalBaseline(
    {
      historical_actuals: [
        { month: "2025-08", actual_amount: 670.2 },
        { month: "2026-08", actual_amount: 999 },
      ],
    },
    "2026-08"
  );
  assert.equal(oneAugust.count, 1);
  assert.equal(oneAugust.average, 670.2);
});

test("full precision is used before display rounding", () => {
  const change = changeVsHistory(699.66, 653.92);
  assert.equal(change.amount, 699.66 - 653.92);
  assert.equal(change.percent, ((699.66 - 653.92) / 653.92) * 100);
  assert.ok(change.percent !== 6.99);
  assert.match(formatChangeVsHistory(change.amount, change.percent, "LKR_MILLIONS"), /\+6\.99%/);
  const specific = changeVsHistory(699.66, 670.2);
  assert.equal(specific.percent, ((699.66 - 670.2) / 670.2) * 100);
});

test("existing predictions and expected ranges remain unchanged", () => {
  const baseline = historicalMonthlyBaseline(sampleForecast);
  const amounts = sampleForecast.monthly_forecasts.map((row) => row.forecast_amount);
  assert.deepEqual(amounts, [680, 699.66, 633.82, 510]);
  assert.equal(historicalAverageCaption(baseline.count), "Overall historical monthly average (42 months)");
  assert.match(pointInsight("2026-08", 45.74, (45.74 / 653.92) * 100, "LKR_MILLIONS"), /Aug-2026/);
  assert.match(chartSource, /Vs Overall Average/);
  assert.match(chartSource, /specificMonthAverageLabel/);
  assert.match(chartSource, /specificMonthChangeLabel/);
  assert.doesNotMatch(chartSource, /Change vs Overall History/);
  assert.doesNotMatch(chartSource, /Overall Historical Average/);
  assert.doesNotMatch(chartSource, /EXPECTED_RANGE_LABEL/);
  assert.match(chartSource, /dataKey="upper"/);
  assert.match(chartSource, /dataKey="lower"/);
  assert.match(resultsSource, /ExpectedRangeLabel/);
  assert.doesNotMatch(chartSource, /previous forecast/);
  assert.doesNotMatch(resultsSource, /forecast_amount\s*[+\-*/]/);
});

test("missing expected-range bounds are not displayed as zero", () => {
  assert.equal(isPresentAmount(null), false);
  assert.equal(isPresentAmount(""), false);
  assert.equal(isPresentAmount(Number.NaN), false);
  assert.equal(isPresentAmount(0), true);
  assert.equal(formatBoundAmount(null, "LKR_MILLIONS"), "Unavailable");
  assert.equal(formatBoundAmount(undefined, "LKR_MILLIONS"), "Unavailable");
  assert.equal(formatBoundAmount(553.46, "LKR_MILLIONS"), "LKR 553.46 Mn");
  assert.match(tableSource, /formatBoundAmount\(row\.lower_bound/);
  assert.match(tableSource, /formatBoundAmount\(row\.upper_bound/);
  assert.doesNotMatch(tableSource, /formatAmount\(row\.lower_bound/);
});

test("generate monthly details uses four aligned columns without lower or upper", () => {
  assert.match(tableSource, /Change vs Overall History/);
  assert.match(tableSource, /Change vs Same Month History/);
  assert.match(tableSource, /const showBounds =\s*!showHistoryComparisons/);
  assert.match(tableSource, /col-change-overall/);
  assert.match(tableSource, /col-change-same/);
  assert.doesNotMatch(tableSource, /col-lower/);
  assert.doesNotMatch(tableSource, /col-upper/);
  assert.match(tableSource, /<ChangeCell meta=\{overallMeta\} \/>/);
  assert.match(tableSource, /<ChangeCell meta=\{specificMeta\} \/>/);
  assert.match(tableSource, /<tr className="total-row">/);
  assert.match(cssSource, /monthly-details-table\.has-comparisons \{/);
  assert.doesNotMatch(cssSource, /has-comparisons\.has-bounds/);
  assert.doesNotMatch(cssSource, /col-lower/);
  assert.match(cssSource, /\.change \{\s*vertical-align: top;/);
  assert.doesNotMatch(cssSource, /\.change \{\s*display: inline-flex/);
  assert.match(chartSource, /dataKey="upper"/);
  assert.match(chartSource, /dataKey="lower"/);
  assert.match(resultsSource, /ExpectedRangeLabel/);
});

test("combined monthly tooltip stays inside the chart card bounds", () => {
  assert.match(chartSource, /placeChartTooltip/);
  assert.match(chartSource, /chart-area-tooltip-host/);
  assert.doesNotMatch(chartSource, /allowEscapeViewBox/);
  assert.match(cssSource, /\.generate-page \.charts \.section-card \{\s*overflow: hidden;/);
  assert.match(cssSource, /\.generate-page \.charts \.section-card-header \{\s*overflow: hidden;/);
  assert.match(chartSource, /chart-tooltip-change/);
});

test("combined monthly tooltip uses forecast-versus labels and compact formatting", () => {
  assert.match(chartSource, /Vs Overall Average/);
  assert.equal(specificMonthChangeLabel("2026-09"), "Vs Sep Average");
  assert.equal(specificMonthAverageLabel("2026-09"), "Historical Sep Average");
  assert.doesNotMatch(chartSource, /Overall Historical Average/);
  assert.doesNotMatch(chartSource, /EXPECTED_RANGE_LABEL/);
  assert.doesNotMatch(chartSource, /Expected Range/);
  assert.match(chartSource, />Forecast</);
  assert.match(chartSource, /specificMonthAverageLabel/);
  assert.equal(formatTooltipChange(41.95, (41.95 / 653.92) * 100, "LKR_MILLIONS"), "LKR +41.95 Mn (+6.42%)");
  assert.equal(formatTooltipChange(-20.1, (-20.1 / 653.92) * 100, "LKR_MILLIONS"), "LKR \u221220.10 Mn (\u22123.07%)");
  assert.equal(formatTooltipChange(0, 0, "LKR_MILLIONS"), "LKR 0.00 Mn (0.00%)");
  assert.equal(formatTooltipChange(10, null, "LKR_MILLIONS"), "Unavailable");
  assert.equal(formatTooltipChange(null, null, "LKR_MILLIONS"), "Unavailable");
});
