import assert from "node:assert/strict";
import test from "node:test";
import {
  analyticsPageMode,
  codeHistoryRanking,
  historyRangeLabel,
  validateHistoricalAnalysis,
} from "./analyticsPage.js";

const sampleHistory = {
  category: { id: "international_settlement", name: "International Settlement" },
  history_start: "2023-01",
  history_end: "2026-06",
  monthly_actuals: [{ month: "2023-01", actual_amount: 10 }],
  yearly_actuals: [{ year: 2023, actual_amount: 10, months_included: 1, year_status: "PARTIAL_YEAR" }],
  seasonal_profile: Array.from({ length: 12 }, (_, index) => ({
    calendar_month: index + 1,
    month_label: "Jan",
    average_amount: 10,
    observation_count: 1,
  })),
  budget_codes: [{ budget_code: "A01", account_name: "Account A01", actual_amount: 10 }],
};

test("historical analytics are shown without a generated forecast", () => {
  const mode = analyticsPageMode({
    historyStatus: "ready",
    history: sampleHistory,
    forecast: null,
  });
  assert.equal(mode.showHistorical, true);
  assert.equal(mode.showForecastAnalysis, false);
  assert.equal(mode.showComparison, false);
  assert.equal(mode.showBudgetTrend, false);
});

test("a missing forecast does not empty the Analytics page", () => {
  const mode = analyticsPageMode({
    historyStatus: "ready",
    history: sampleHistory,
    forecast: null,
  });
  assert.equal(mode.showHistoricalEmpty, false);
  assert.equal(mode.blockPageOnMissingForecast, false);
});

test("forecast analysis is optional and appears only when a forecast exists", () => {
  const withoutForecast = analyticsPageMode({
    historyStatus: "ready",
    history: sampleHistory,
    forecast: null,
  });
  const withForecast = analyticsPageMode({
    historyStatus: "ready",
    history: sampleHistory,
    forecast: { overall_total: 12 },
  });
  assert.equal(withoutForecast.showForecastAnalysis, false);
  assert.equal(withoutForecast.showComparison, false);
  assert.equal(withoutForecast.showBudgetTrend, false);
  assert.equal(withForecast.showForecastAnalysis, true);
  assert.equal(withForecast.showComparison, true);
  assert.equal(withForecast.showBudgetTrend, true);
});

test("historical vs forecast comparison does not block historical analytics", () => {
  const mode = analyticsPageMode({
    historyStatus: "ready",
    history: sampleHistory,
    forecast: null,
  });
  assert.equal(mode.showHistorical, true);
  assert.equal(mode.showComparison, false);
  assert.equal(mode.showBudgetTrend, false);
  assert.equal(mode.blockPageOnMissingForecast, false);
});

test("history range uses stored actual months", () => {
  assert.equal(historyRangeLabel("2023-01", "2026-06"), "Jan-23 – Jun-26");
});

test("recurring budget codes keep rank by historical actual total", () => {
  const ranking = codeHistoryRanking([
    { budget_code: "B02", account_name: "Second", actual_amount: 20, recurring: true },
    { budget_code: "A01", account_name: "First", actual_amount: 80, recurring: true },
  ]);
  assert.equal(ranking[0].budget_code, "A01");
  assert.equal(ranking[0].rank, 1);
  assert.equal(ranking[0].share, 0.8);
});

test("invalid historical analysis is rejected before render", () => {
  assert.equal(validateHistoricalAnalysis(sampleHistory), "");
  assert.equal(
    validateHistoricalAnalysis({ ...sampleHistory, monthly_actuals: [] }),
    "Monthly actuals are missing."
  );
});

test("historical charts stay visible while another category is loading", () => {
  const mode = analyticsPageMode({
    historyStatus: "loading",
    history: sampleHistory,
    forecast: null,
  });
  assert.equal(mode.showHistorical, true);
  assert.equal(mode.showHistoricalLoading, false);
  assert.equal(mode.blockPageOnMissingForecast, false);
});
