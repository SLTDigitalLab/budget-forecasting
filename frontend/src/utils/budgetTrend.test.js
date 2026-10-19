import assert from "node:assert/strict";
import test from "node:test";
import { buildBudgetTrend, seriesDirection } from "./budgetTrend.js";

test("budget trend keeps historical actuals and predicted months on one timeline", () => {
  const trend = buildBudgetTrend({
    monthlyActuals: [
      { month: "2026-05", actual_amount: 100 },
      { month: "2026-06", actual_amount: 110 },
    ],
    forecast: {
      monthly_average: 125,
      monthly_forecasts: [
        { month: "2026-07", forecast_amount: 120, lower_bound: 110, upper_bound: 130 },
        { month: "2026-08", forecast_amount: 130, lower_bound: null, upper_bound: null, interval_status: "unavailable_beyond_calibrated_horizon" },
      ],
    },
  });
  assert.equal(trend.points.length, 4);
  assert.equal(trend.points[0].actual, 100);
  assert.equal(trend.points[0].forecast, null);
  assert.equal(trend.points[2].forecast, 120);
  assert.equal(trend.points[2].actual, null);
  assert.equal(trend.points[2].lower, 110);
  assert.equal(trend.points[2].upper, 130);
  assert.equal(trend.points[3].interval_status, "unavailable_beyond_calibrated_horizon");
  assert.equal(trend.forecast_average, 125);
  assert.equal(trend.historical_average, 105);
  assert.equal(trend.difference, 20);
  assert.equal(trend.has_expected_range, true);
  assert.equal(trend.historical_trend.label, "Rising");
  assert.equal(trend.forecast_trend.label, "Rising");
});

test("budget trend does not overlap a forecast month that already has an actual", () => {
  const trend = buildBudgetTrend({
    monthlyActuals: [{ month: "2026-06", actual_amount: 90 }],
    forecast: {
      monthly_forecasts: [
        { month: "2026-06", forecast_amount: 999 },
        { month: "2026-07", forecast_amount: 100 },
      ],
    },
  });
  assert.equal(trend.points.length, 2);
  assert.equal(trend.points[0].month, "2026-06");
  assert.equal(trend.points[0].actual, 90);
  assert.equal(trend.points[0].forecast, null);
  assert.equal(trend.points[1].month, "2026-07");
  assert.equal(trend.forecast_month_count, 1);
});

test("budget trend is empty without a generated forecast", () => {
  const trend = buildBudgetTrend({
    monthlyActuals: [{ month: "2026-06", actual_amount: 90 }],
    forecast: null,
  });
  assert.equal(trend.points.length, 1);
  assert.equal(trend.forecast_month_count, 0);
  assert.equal(trend.forecast_average, null);
});

test("series direction is stable when the start and end are almost unchanged", () => {
  assert.equal(seriesDirection([100, 100.2]).label, "Stable");
  assert.equal(seriesDirection([100]).label, "Not enough months");
  assert.equal(seriesDirection([200, 100]).label, "Falling");
});
