import assert from "node:assert/strict";
import test from "node:test";
import {
  buildForecastComparison,
  comparisonTotals,
  historicalPeriodsForForecastMonth,
  mergeHistoricalActuals,
} from "./forecastComparison.js";

const historicalActuals = [
  { month: "2023-07", actual_amount: 700 },
  { month: "2024-07", actual_amount: 800 },
  { month: "2025-07", actual_amount: 750 },
  { month: "2023-08", actual_amount: 500 },
  { month: "2024-08", actual_amount: 520 },
];

test("July forecast uses every prior July actual in the historical average", () => {
  const rows = buildForecastComparison({
    historicalActuals,
    forecast: {
      monthly_forecasts: [{ month: "2026-07", forecast_amount: 780 }],
    },
    historyEnd: "2026-06",
  });
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].historical_periods_used, [
    { month: "2023-07", actual: 700 },
    { month: "2024-07", actual: 800 },
    { month: "2025-07", actual: 750 },
  ]);
  assert.equal(rows[0].historical_average, 750);
  assert.equal(rows[0].forecast_amount, 780);
  assert.equal(rows[0].difference, 30);
  assert.equal(rows[0].observation_count, 3);
});

test("the forecast month itself is not used as a historical baseline", () => {
  const periods = historicalPeriodsForForecastMonth({
    forecastMonth: "2026-07",
    actualsByMonth: new Map([
      ["2025-07", 750],
      ["2026-07", 999],
    ]),
    historyEnd: "2026-06",
  });
  assert.deepEqual(periods, [{ month: "2025-07", actual: 750 }]);
});

test("actuals after history end are excluded from the comparison baseline", () => {
  const rows = buildForecastComparison({
    historicalActuals: [
      { month: "2025-07", actual_amount: 750 },
      { month: "2026-07", actual_amount: 900 },
    ],
    forecast: {
      monthly_forecasts: [{ month: "2026-07", forecast_amount: 780 }],
    },
    historyEnd: "2026-06",
  });
  assert.deepEqual(rows[0].historical_periods_used, [{ month: "2025-07", actual: 750 }]);
});

test("stored analysis actuals are preferred when merging forecast history", () => {
  const merged = mergeHistoricalActuals(
    [{ month: "2025-07", actual_amount: 750 }],
    [{ month: "2025-07", actual_amount: 1 }, { month: "2024-07", actual_amount: 800 }]
  );
  assert.deepEqual(merged, [
    { month: "2024-07", actual_amount: 800 },
    { month: "2025-07", actual_amount: 750 },
  ]);
});

test("comparison totals sum monthly historical averages against the forecast", () => {
  const totals = comparisonTotals([
    {
      month: "2026-07",
      forecast_amount: 780,
      historical_average: 750,
      difference: 30,
    },
    {
      month: "2026-08",
      forecast_amount: 820,
      historical_average: 510,
      difference: 310,
    },
  ]);
  assert.equal(totals.forecast_total, 1600);
  assert.equal(totals.historical_total, 1260);
  assert.equal(totals.difference, 340);
  assert.equal(totals.month_count, 2);
});
