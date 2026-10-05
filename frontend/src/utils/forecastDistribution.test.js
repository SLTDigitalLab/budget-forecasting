import assert from "node:assert/strict";
import test from "node:test";
import { forecastDistributionByCode } from "./forecastDistribution.js";

test("forecast distribution totals each Budget Code across months", () => {
  const slices = forecastDistributionByCode({
    account_monthly_forecasts: [
      { budget_code: "A02", account_name: "Second", month: "2026-07", forecast_amount: 20 },
      { budget_code: "A01", account_name: "First", month: "2026-07", forecast_amount: 30 },
      { budget_code: "A01", account_name: "First", month: "2026-08", forecast_amount: 50 },
    ],
  });
  assert.equal(slices.length, 2);
  assert.equal(slices[0].budget_code, "A01");
  assert.equal(slices[0].forecast_amount, 80);
  assert.equal(slices[0].percent, 0.8);
  assert.equal(slices[1].budget_code, "A02");
  assert.equal(slices[1].forecast_amount, 20);
  assert.equal(slices[1].percent, 0.2);
  assert.equal(slices[0].total, 100);
});

test("forecast distribution ignores non-finite and missing codes", () => {
  const slices = forecastDistributionByCode({
    account_monthly_forecasts: [
      { budget_code: "A01", month: "2026-07", forecast_amount: 10 },
      { budget_code: "", month: "2026-07", forecast_amount: 99 },
      { budget_code: "A02", month: "2026-07", forecast_amount: Number.NaN },
    ],
  });
  assert.equal(slices.length, 1);
  assert.equal(slices[0].budget_code, "A01");
});

test("forecast distribution is empty without account forecasts", () => {
  assert.deepEqual(forecastDistributionByCode(null), []);
  assert.deepEqual(forecastDistributionByCode({}), []);
});
