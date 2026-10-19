import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  AMOUNT_TICK_Y_AXIS_WIDTH,
  amountChartMargin,
  amountTickYAxisProps,
  cartesianChartMargin,
  CARTESIAN_CHART_MARGIN,
  VALUE_AXIS_TITLE_OFFSET,
  VALUE_Y_AXIS_TICK_MARGIN,
  VALUE_Y_AXIS_WIDTH,
  valueAxisTitle,
  valueYAxisProps,
  yAxisFitsTicks,
} from "./chartLayout.js";
import { formatAmount, formatAxisTick } from "./forecastDisplay.js";

const root = dirname(fileURLToPath(import.meta.url));
const sources = {
  overview: readFileSync(resolve(root, "../pages/Overview.jsx"), "utf8"),
  analytics: readFileSync(resolve(root, "../pages/Analytics.jsx"), "utf8"),
  comparison: readFileSync(resolve(root, "../components/ForecastComparison.jsx"), "utf8"),
  trend: readFileSync(resolve(root, "../components/BudgetTrendAnalysis.jsx"), "utf8"),
  monthly: readFileSync(resolve(root, "../components/MonthlyForecastChart.jsx"), "utf8"),
  report: readFileSync(resolve(root, "../components/ForecastReportPreview.jsx"), "utf8"),
};

const SAMPLE_TICKS = ["-102.82", "1,625.60", "10,104.26", "1,000", "1,000,000"];

test("value Y-axis title sits left of ticks instead of on top of them", () => {
  const label = valueAxisTitle("LKR Mn");
  assert.equal(label.value, "LKR Mn");
  assert.equal(label.angle, -90);
  assert.equal(label.position, "insideLeft");
  assert.ok(VALUE_AXIS_TITLE_OFFSET <= 8);
  assert.ok(VALUE_Y_AXIS_WIDTH >= 88);
  assert.ok(VALUE_Y_AXIS_TICK_MARGIN >= 6);
  assert.ok(CARTESIAN_CHART_MARGIN.left >= 8);
  const axis = valueYAxisProps("LKR Mn");
  assert.equal(axis.width, VALUE_Y_AXIS_WIDTH);
  assert.equal(axis.label.offset, VALUE_AXIS_TITLE_OFFSET);
  assert.ok(axis.width - VALUE_AXIS_TITLE_OFFSET - 16 > 36);
});

test("shared Y-axis width fits large positive and negative tick labels", () => {
  assert.equal(yAxisFitsTicks(SAMPLE_TICKS), true);
  assert.equal(formatAxisTick(-102.82), "-103");
  assert.equal(formatAxisTick(1625.6), "1,626");
  assert.equal(formatAxisTick(10104.26), "10,104");
  assert.equal(formatAxisTick(1000000), "1,000,000");
  assert.equal(formatAmount(-102.81913757324219, "LKR_MILLIONS"), "LKR -102.82 Mn");
});

test("chart margins keep axis titles inside the card", () => {
  const margin = cartesianChartMargin();
  assert.equal(margin.left, CARTESIAN_CHART_MARGIN.left);
  assert.equal(margin.top, 12);
  assert.deepEqual(cartesianChartMargin({ bottom: 28 }).bottom, 28);
  assert.ok(amountChartMargin().left >= 8);
  assert.ok(amountTickYAxisProps().width >= 140);
  assert.ok(AMOUNT_TICK_Y_AXIS_WIDTH >= 140);
});

test("cartesian charts reuse the shared axis layout helper", () => {
  assert.match(sources.overview, /valueYAxisProps\(historicalUnit\)/);
  assert.match(sources.overview, /valueYAxisProps\(forecastUnit\)/);
  assert.match(sources.overview, /cartesianChartMargin\(\)/);
  assert.doesNotMatch(sources.overview, /valueXAxisTitle\(unit\)/);
  assert.match(sources.analytics, /valueYAxisProps\(unit\)/);
  assert.match(sources.comparison, /valueYAxisProps\(unit\)/);
  assert.match(sources.trend, /valueYAxisProps\(unit\)/);
  assert.match(sources.monthly, /amountTickYAxisProps\(\)/);
  assert.match(sources.report, /MonthlyForecastChart/);
  assert.doesNotMatch(sources.overview, /width=\{48\}/);
  assert.doesNotMatch(sources.overview, /position: "insideLeft", offset: 10/);
  assert.doesNotMatch(sources.analytics, /width=\{52\}/);
  assert.doesNotMatch(sources.comparison, /width=\{52\}/);
  assert.doesNotMatch(sources.trend, /width=\{52\}/);
});
