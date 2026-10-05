import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(root, "../styles/global.css"), "utf8");
const files = {
  forecastTable: readFileSync(resolve(root, "../components/ForecastTable.jsx"), "utf8"),
  budgetCodeTable: readFileSync(resolve(root, "../components/BudgetCodeForecastTable.jsx"), "utf8"),
  history: readFileSync(resolve(root, "../pages/ForecastHistory.jsx"), "utf8"),
  comparison: readFileSync(resolve(root, "../components/ForecastComparison.jsx"), "utf8"),
  report: readFileSync(resolve(root, "../components/ForecastReportPreview.jsx"), "utf8"),
  analytics: readFileSync(resolve(root, "../pages/Analytics.jsx"), "utf8"),
  settings: `${readFileSync(resolve(root, "../pages/Settings.jsx"), "utf8")}\n${readFileSync(resolve(root, "../components/HistoricalDataPanels.jsx"), "utf8")}`,
  overview: readFileSync(resolve(root, "../pages/Overview.jsx"), "utf8"),
};

test("main content is the single primary vertical page scroll", () => {
  assert.match(css, /\.app-shell \{[\s\S]*overflow:\s*hidden/);
  assert.match(css, /\.content \{[\s\S]*min-height:\s*0/);
  assert.match(css, /\.content \{[\s\S]*overflow-y:\s*auto/);
  assert.match(css, /\.content \{[\s\S]*overflow-x:\s*clip/);
  assert.match(css, /\.table-wrap \{[\s\S]*overflow-x:\s*auto/);
  assert.match(css, /\.table-wrap \{[\s\S]*overflow-y:\s*clip/);
  assert.doesNotMatch(css, /overscroll-behavior:\s*contain/);
  assert.doesNotMatch(css, /overscroll-behavior:\s*none/);
  assert.doesNotMatch(css, /scrollbar-width:\s*none/);
});

test("shared data-scroll keeps long content inside a bounded viewport", () => {
  assert.match(css, /\.data-scroll \{[\s\S]*max-height:\s*560px/);
  assert.match(css, /\.data-scroll-sm \{[\s\S]*max-height:\s*420px/);
  assert.match(css, /\.data-scroll-lg \{[\s\S]*max-height:\s*640px/);
  assert.match(css, /\.data-scroll \{[\s\S]*overflow-y:\s*auto/);
  assert.match(css, /\.data-scroll \{[\s\S]*overscroll-behavior:\s*auto/);
  assert.match(css, /\.table-wrap\.data-scroll > table thead th \{[\s\S]*position:\s*sticky/);
  assert.match(css, /scrollbar-width:\s*thin/);
});

test("long tables and Budget Code lists scroll without truncating records", () => {
  assert.match(files.forecastTable, /className="table-wrap"/);
  assert.doesNotMatch(files.forecastTable, /data-scroll/);
  assert.match(files.budgetCodeTable, /table-wrap data-scroll data-scroll-lg/);
  assert.match(files.history, /className="table-wrap"/);
  assert.doesNotMatch(files.history, /data-scroll/);
  assert.match(files.comparison, /className="table-wrap"/);
  assert.doesNotMatch(files.comparison, /data-scroll/);
  assert.match(files.report, /table-wrap data-scroll data-scroll-lg/);
  assert.doesNotMatch(files.report, /data-scroll data-scroll-sm/);
  assert.match(files.analytics, /Scrollable Budget Code ranking/);
  assert.match(files.analytics, /Scrollable recurring expense Budget Codes/);
  assert.match(files.settings, /settings-wide-table data-scroll data-scroll-lg/);
  assert.match(files.overview, /overview-forecast-code-list-scroll data-scroll/);
  assert.doesNotMatch(files.budgetCodeTable, /rows\.slice\(0,\s*10\)/);
  assert.doesNotMatch(files.analytics, /ranking\.slice\(0,\s*10\)/);
});
