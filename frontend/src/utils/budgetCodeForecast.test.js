import assert from "node:assert/strict";
import test from "node:test";
import {
  BUDGET_CODE_FORECAST_ERROR,
  HIDE_BUDGET_CODE_FORECAST,
  TRAINED_BUDGET_CODE_COUNT,
  VIEW_BUDGET_CODE_FORECAST,
  budgetCodeForecastDisclosure,
  buildBudgetCodeMonthForecast,
  fullMonthLabel,
  isSingleMonthForecast,
} from "./budgetCodeForecast.js";

function twelveCodes(month = "2027-07") {
  const selected_accounts = Array.from({ length: TRAINED_BUDGET_CODE_COUNT }, (_, index) => (
    `A${String(index + 1).padStart(2, "0")}`
  ));
  const account_monthly_forecasts = selected_accounts.map((code, index) => ({
    budget_code: code,
    account_name: `Account ${code}`,
    month,
    forecast_amount: (12 - index) * 10,
    lower_bound: (12 - index) * 8,
    upper_bound: (12 - index) * 12,
  }));
  const combined = account_monthly_forecasts.reduce((sum, row) => sum + row.forecast_amount, 0);
  return {
    selected_accounts,
    selected_account_count: TRAINED_BUDGET_CODE_COUNT,
    requested_start_month: month,
    requested_end_month: month,
    overall_total: combined,
    monthly_forecasts: [
      { month, forecast_amount: combined, lower_bound: 500, upper_bound: 900 },
    ],
    account_monthly_forecasts,
  };
}

test("July 2027 uses the twelve trained Budget Codes and ranks them", () => {
  const result = buildBudgetCodeMonthForecast(twelveCodes("2027-07"));
  assert.equal(result.error, "");
  assert.equal(result.title, "Budget Code-wise Forecast — July 2027");
  assert.equal(result.amount_column, "July 2027 Forecast Amount");
  assert.equal(result.rows.length, 12);
  assert.equal(result.rows[0].rank, 1);
  assert.equal(result.rows[0].budget_code, "A01");
  assert.equal(result.rows[0].account_name, "Account A01");
  assert.equal(result.rows[0].forecast_amount, 120);
  assert.equal(result.rows[11].budget_code, "A12");
  assert.equal(result.rows[11].rank, 12);
  assert.ok(!result.rows.some((row) => String(row.budget_code).toLowerCase() === "other"));
  assert.ok(!result.rows.some((row) => /direct cost/i.test(row.account_name)));
});

test("combined July 2027 forecast equals the sum of the twelve Budget Code forecasts", () => {
  const forecast = twelveCodes("2027-07");
  const result = buildBudgetCodeMonthForecast(forecast);
  const accounted = result.rows.reduce((sum, row) => sum + row.forecast_amount, 0);
  assert.equal(result.combined_total, accounted);
  assert.equal(result.combined_total, forecast.overall_total);
  assert.equal(result.combined_total, forecast.monthly_forecasts[0].forecast_amount);
  const percentTotal = result.rows.reduce((sum, row) => sum + row.contribution_percent, 0);
  assert.ok(Math.abs(percentTotal - 100) < 1e-6);
});

test("expected range bounds are kept per Budget Code when available", () => {
  const result = buildBudgetCodeMonthForecast(twelveCodes("2027-07"));
  assert.equal(result.rows[0].lower_bound, 96);
  assert.equal(result.rows[0].upper_bound, 144);
  assert.equal(result.combined_lower_bound, 500);
  assert.equal(result.combined_upper_bound, 900);
});

test("full month labels use the complete month name", () => {
  assert.equal(fullMonthLabel("2027-07"), "July 2027");
  assert.equal(isSingleMonthForecast({ requested_start_month: "2027-07", requested_end_month: "2027-07" }), true);
  assert.equal(isSingleMonthForecast({ requested_start_month: "2027-07", requested_end_month: "2027-12" }), false);
});

test("a multi-month forecast does not build the specific-month Budget Code table", () => {
  const forecast = twelveCodes("2027-07");
  forecast.requested_end_month = "2027-12";
  assert.equal(buildBudgetCodeMonthForecast(forecast), null);
});

test("Other and untrained Budget Codes are not displayed", () => {
  const forecast = twelveCodes("2027-07");
  forecast.account_monthly_forecasts.push(
    { budget_code: "Other", account_name: "Other", month: "2027-07", forecast_amount: 999 },
    { budget_code: "DIRECT", account_name: "Direct Cost", month: "2027-07", forecast_amount: 888 },
  );
  const result = buildBudgetCodeMonthForecast(forecast);
  assert.equal(result.rows.length, 12);
  assert.ok(!result.rows.some((row) => row.budget_code === "Other"));
  assert.ok(!result.rows.some((row) => row.budget_code === "DIRECT"));
  assert.equal(result.combined_total, forecast.overall_total);
});

test("a missing trained Budget Code is not replaced with another category", () => {
  const forecast = twelveCodes("2027-07");
  forecast.account_monthly_forecasts = forecast.account_monthly_forecasts.filter((row) => row.budget_code !== "A12");
  forecast.account_monthly_forecasts.push({
    budget_code: "DIRECT",
    account_name: "Direct Cost",
    month: "2027-07",
    forecast_amount: 10,
  });
  const result = buildBudgetCodeMonthForecast(forecast);
  assert.equal(result.error, BUDGET_CODE_FORECAST_ERROR);
  assert.equal(result.rows.length, 0);
});

test("a combined total that does not match the twelve account forecasts is rejected", () => {
  const forecast = twelveCodes("2027-07");
  forecast.monthly_forecasts[0].forecast_amount += 50;
  forecast.overall_total += 50;
  const result = buildBudgetCodeMonthForecast(forecast);
  assert.equal(result.error, BUDGET_CODE_FORECAST_ERROR);
  assert.equal(result.rows.length, 0);
});

test("the Budget Code-wise forecast stays hidden until the user chooses to view it", () => {
  const closed = budgetCodeForecastDisclosure({ forecast: twelveCodes("2027-07"), expanded: false });
  assert.equal(closed.showAction, true);
  assert.equal(closed.showTable, false);
  assert.equal(closed.actionLabel, VIEW_BUDGET_CODE_FORECAST);
  assert.equal(closed.usesExistingResponse, true);
});

test("viewing Budget Code-wise forecast reveals already-returned rows without refetching", () => {
  const open = budgetCodeForecastDisclosure({ forecast: twelveCodes("2027-07"), expanded: true });
  assert.equal(open.showTable, true);
  assert.equal(open.actionLabel, HIDE_BUDGET_CODE_FORECAST);
  assert.equal(open.usesExistingResponse, true);
});

test("a multi-month forecast does not show the optional Budget Code-wise action", () => {
  const forecast = twelveCodes("2027-07");
  forecast.requested_end_month = "2027-12";
  const view = budgetCodeForecastDisclosure({ forecast, expanded: true });
  assert.equal(view.showAction, false);
  assert.equal(view.showTable, false);
});

test("Budget Code-wise output includes fitted, zero-policy, and unavailable rows", () => {
  const month = "2026-07";
  const forecast = {
    selected_accounts: ["511101", "511105", "523318"],
    selected_account_count: 3,
    requested_start_month: month,
    requested_end_month: month,
    overall_total: 25,
    partial_forecast: true,
    monthly_forecasts: [{ month, forecast_amount: 25 }],
    budget_code_forecasts: [
      {
        budget_code: "511101",
        account_name: "Fitted account",
        available: true,
        forecast: { [month]: 25 },
        algorithm: "XGBoost",
        display_status: "Available",
        forecast_type: "FITTED_MODEL",
      },
      {
        budget_code: "511105",
        account_name: "Zero account",
        available: true,
        forecast: { [month]: 0 },
        algorithm: null,
        display_status: "Zero Policy",
        forecast_type: "ZERO_POLICY",
      },
      {
        budget_code: "523318",
        account_name: "Unavailable account",
        available: false,
        forecast: null,
        algorithm: null,
        display_status: "Forecast Unavailable",
        reason: "Insufficient historical data for reliable model evaluation.",
        source_status: "NO_VALID_WINNER",
      },
    ],
  };
  const result = buildBudgetCodeMonthForecast(forecast);
  assert.equal(result.error, "");
  assert.equal(result.rows.length, 3);
  assert.equal(result.combined_total, 25);
  const fitted = result.rows.find((row) => row.budget_code === "511101");
  const zero = result.rows.find((row) => row.budget_code === "511105");
  const missing = result.rows.find((row) => row.budget_code === "523318");
  assert.equal(fitted.forecast_amount, 25);
  assert.equal(fitted.algorithm, "XGBoost");
  assert.equal(fitted.display_status, "Available");
  assert.equal(zero.forecast_amount, 0);
  assert.equal(zero.algorithm, null);
  assert.equal(zero.display_status, "Zero Policy");
  assert.equal(missing.forecast_amount, null);
  assert.equal(missing.available, false);
  assert.equal(missing.display_status, "Forecast Unavailable");
  assert.match(missing.reason, /Insufficient historical data/);
});
