import assert from "node:assert/strict";
import test from "node:test";

import { buildForecastCoverage } from "./forecastCoverage.js";

test("coverage counts ZERO_POLICY as covered and reports unavailable codes", () => {
  const coverage = buildForecastCoverage({
    partial_forecast: true,
    selected_account_count: 20,
    forecast_coverage: {
      total_budget_codes: 20,
      forecasted_budget_codes: 18,
      zero_policy_budget_codes: 2,
      unavailable_budget_codes: 2,
    },
  });
  assert.equal(coverage.message, "Forecast coverage: 18 / 20 Budget Codes");
  assert.match(coverage.detail, /2 Budget Codes unavailable/);
  assert.equal(coverage.partial, true);
  assert.equal(coverage.zeroPolicy, 2);
});

test("All Categories coverage uses the overall counts", () => {
  const coverage = buildForecastCoverage({
    partial_forecast: true,
    forecast_coverage: {
      total_budget_codes: 458,
      forecasted_budget_codes: 448,
      zero_policy_budget_codes: 69,
      unavailable_budget_codes: 10,
    },
  });
  assert.equal(coverage.message, "Forecast coverage: 448 / 458 Budget Codes");
  assert.match(coverage.detail, /10 Budget Codes unavailable due to insufficient historical data/);
});
