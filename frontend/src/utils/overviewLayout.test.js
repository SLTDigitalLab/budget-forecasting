import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const overviewSource = readFileSync(resolve(root, "../pages/Overview.jsx"), "utf8");
const cssSource = readFileSync(resolve(root, "../styles/global.css"), "utf8");

test("Overview line charts keep the unit title off the Y-axis ticks", () => {
  assert.match(overviewSource, /valueYAxisProps\(historicalUnit\)/);
  assert.match(overviewSource, /valueYAxisProps\(forecastUnit\)/);
  assert.match(overviewSource, /cartesianChartMargin\(\)/);
  assert.doesNotMatch(overviewSource, /valueXAxisTitle\(unit\)/);
  assert.doesNotMatch(overviewSource, /width=\{48\}/);
  assert.doesNotMatch(overviewSource, /position: "insideLeft", offset: 10/);
  assert.doesNotMatch(overviewSource, /ComposedChart/);
});

test("Budget Code distribution scrolls the full list inside a fixed card", () => {
  assert.match(overviewSource, /overview-forecast-code-list-scroll data-scroll/);
  assert.match(overviewSource, /slices\.map\(\(row\) =>/);
  assert.doesNotMatch(overviewSource, /distribution\.slice\(0,\s*10\)/);
  assert.doesNotMatch(overviewSource, /\.slice\(0,\s*10\)/);
  assert.match(cssSource, /\.data-scroll \{[\s\S]*overflow-y:\s*auto/);
  assert.match(cssSource, /\.overview-distribution \.overview-forecast-code-list-scroll \{[\s\S]*max-height:\s*248px/);
});

test("category Overview uses two balanced rows and no old category bar card", () => {
  assert.doesNotMatch(overviewSource, /overview-category-scroll data-scroll/);
  assert.doesNotMatch(overviewSource, /CATEGORY_BAR_ROW_PX/);
  assert.doesNotMatch(overviewSource, /Category-wise Forecast/);
  assert.doesNotMatch(overviewSource, /Latest Month Budget Share/);
  assert.match(cssSource, /\.overview-split \{[\s\S]*grid-template-columns:\s*minmax\(0, 65fr\) minmax\(240px, 35fr\)/);
});
