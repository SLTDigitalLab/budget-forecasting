import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  REPORT_LAYOUT,
  UNAVAILABLE,
  buildReportPreviewModel,
  exportPageState,
  formatExpectedBound,
  excelFileStem,
  reportFileStem,
} from "./forecastReport.js";

const sample = {
  requested_start_month: "2026-07",
  requested_end_month: "2026-12",
  generated_at: "2026-08-30T10:00:00Z",
  category: "Int'l Settlement",
  forecast_month_count: 2,
  selected_account_count: 2,
  selected_accounts: ["A01", "A02"],
  overall_total: 250,
  monthly_average: 125,
  minimum_monthly_forecast: 120,
  maximum_monthly_forecast: 130,
  amount_unit: "LKR_MILLIONS",
  history_end: "2026-06",
  historical_actuals: [
    { month: "2025-07", actual_amount: 100 },
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

test("preview uses the selected completed forecast totals", () => {
  const preview = buildReportPreviewModel(sample);
  assert.equal(preview.overall_total, 250);
  assert.equal(preview.summary.overall_total, 250);
  assert.equal(preview.header.period, "Jul-2026 to Dec-2026");
  assert.equal(preview.header.category, "Int'l Settlement");
  assert.deepEqual(preview.selected_accounts, ["A01", "A02"]);
});

test("preview does not start a download", () => {
  const preview = buildReportPreviewModel(sample);
  assert.equal(preview.triggersDownload, false);
  assert.equal(exportPageState({ selectedId: 7, previewStatus: "ready" }).previewStartsDownload, false);
});

test("PDF and Excel buttons appear only after preview loads", () => {
  assert.equal(exportPageState({ selectedId: 7, previewStatus: "idle" }).showDownloads, false);
  assert.equal(exportPageState({ selectedId: 7, previewStatus: "loading" }).showDownloads, false);
  assert.equal(exportPageState({ selectedId: 7, previewStatus: "ready" }).showDownloads, true);
  assert.equal(exportPageState({ selectedId: "", previewStatus: "ready" }).showDownloads, false);
});

test("empty state is shown before a completed forecast is selected", () => {
  assert.equal(
    exportPageState({ selectedId: "", previewStatus: "idle" }).emptyMessage,
    "No completed forecast selected"
  );
});

test("old forecasts with null bounds display Unavailable", () => {
  const preview = buildReportPreviewModel(sample);
  assert.equal(preview.monthly_forecasts[1].lower_bound, UNAVAILABLE);
  assert.equal(preview.monthly_forecasts[1].upper_bound, UNAVAILABLE);
  assert.equal(formatExpectedBound(null, () => "LKR 0.00 Mn", "LKR_MILLIONS"), UNAVAILABLE);
  assert.equal(formatExpectedBound(0, () => "zero", "LKR_MILLIONS"), "zero");
});

test("preview filenames match the export naming scheme", () => {
  assert.equal(reportFileStem(sample), "SLT_Mobitel_Forecast_Report_Jul-2026_to_Dec-2026");
  assert.equal(excelFileStem(sample), "AI_Budget_Forecast_Jul_2026_Dec_2026");
  assert.equal(buildReportPreviewModel(sample).excel_filename_stem, "AI_Budget_Forecast_Jul_2026_Dec_2026");
});

test("budget codes come only from the saved forecast run", () => {
  const preview = buildReportPreviewModel({
    ...sample,
    selected_accounts: ["A02"],
    account_monthly_forecasts: [
      ...sample.account_monthly_forecasts,
      { budget_code: "R99", account_name: "Rival", month: "2026-07", forecast_amount: 900 },
    ],
  });
  assert.deepEqual(preview.budget_codes.map((row) => row.budget_code), ["A02"]);
  assert.equal(preview.budget_codes.some((row) => row.budget_code === "R99"), false);
});

test("preview comparison uses the same stored-run historical logic as Analytics", () => {
  const preview = buildReportPreviewModel(sample);
  assert.equal(preview.comparison[0].month, "2026-07");
  assert.equal(preview.comparison[0].historical_average, 100);
  assert.equal(preview.comparison[0].forecast_amount, 120);
  assert.equal(preview.comparison[0].observation_count, 1);
});

test("mobile report layout keeps the page from overflowing horizontally", () => {
  assert.equal(REPORT_LAYOUT.pageOverflowX, "clip");
  assert.equal(REPORT_LAYOUT.tableOverflowX, "auto");
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../styles/global.css"), "utf8");
  assert.match(css, /\.export-page\s*\{[^}]*overflow-x:\s*clip/);
  assert.match(css, /\.export-report\s+\.table-wrap\s*\{[^}]*overflow-x:\s*auto/);
});
