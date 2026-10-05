import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_TOOLTIP_HEIGHT,
  DEFAULT_TOOLTIP_WIDTH,
  TOOLTIP_GAP,
  placeChartTooltip,
  resolveTooltipWidth,
} from "./chartTooltipPlacement.js";
import {
  calendarMonthShortName,
  formatTooltipChange,
  specificMonthAverageLabel,
  specificMonthChangeLabel,
} from "./forecastHistoryBaseline.js";

const here = dirname(fileURLToPath(import.meta.url));
const chartSource = readFileSync(resolve(here, "../components/MonthlyForecastChart.jsx"), "utf8");
const cssSource = readFileSync(resolve(here, "../styles/global.css"), "utf8");
const resultsSource = readFileSync(resolve(here, "../components/ForecastResults.jsx"), "utf8");

const WIDE = { containerWidth: 800, containerHeight: 270, tooltipWidth: 280, tooltipHeight: 210 };
const LEFT_PAD = 136;
const RIGHT_PAD = 16;

function plotPointX(index, count, containerWidth = WIDE.containerWidth) {
  const plotWidth = Math.max(0, containerWidth - LEFT_PAD - RIGHT_PAD);
  if (count <= 1) {
    return LEFT_PAD;
  }
  return LEFT_PAD + (plotWidth * index) / (count - 1);
}

function assertInside(placement, containerWidth = WIDE.containerWidth, containerHeight = WIDE.containerHeight) {
  assert.ok(placement.x >= 0);
  assert.ok(placement.y >= 0);
  assert.ok(placement.x + placement.width <= containerWidth + 1e-9);
  assert.ok(placement.y + placement.height <= containerHeight + 1e-9);
}

test("first forecast point opens to the right and stays inside the chart", () => {
  const pointX = plotPointX(0, 48);
  const placed = placeChartTooltip({ ...WIDE, pointX, pointY: 90 });
  assert.equal(placed.side, "right");
  assert.equal(placed.x, pointX + TOOLTIP_GAP);
  assertInside(placed);
});

test("centre forecast point uses the preferred right-hand position", () => {
  const pointX = plotPointX(23, 48);
  const placed = placeChartTooltip({ ...WIDE, pointX, pointY: 120 });
  assert.equal(placed.side, "right");
  assert.equal(placed.x, pointX + TOOLTIP_GAP);
  assert.ok(placed.x > 200);
  assertInside(placed);
});

test("last forecast point flips left so Dec-2030 stays fully visible", () => {
  const pointX = plotPointX(47, 48);
  const placed = placeChartTooltip({ ...WIDE, pointX, pointY: 80 });
  assert.equal(placed.side, "left");
  assert.equal(placed.x, pointX - placed.width - TOOLTIP_GAP);
  assert.ok(placed.x + placed.width <= pointX - TOOLTIP_GAP + 1e-9);
  assertInside(placed);
});

test("right-edge point in a 48-month forecast never opens past the card", () => {
  const count = 48;
  const lastX = plotPointX(count - 1, count);
  const placed = placeChartTooltip({ ...WIDE, pointX: lastX, pointY: 140 });
  assert.equal(count, 48);
  assert.ok(lastX > 700);
  assert.equal(placed.side, "left");
  assertInside(placed);
  assert.ok(placed.x + placed.width <= WIDE.containerWidth);
});

test("narrow container keeps the tooltip inside the chart width", () => {
  const containerWidth = 320;
  const containerHeight = 270;
  const width = resolveTooltipWidth(containerWidth, 300);
  assert.equal(width, Math.min(300, containerWidth - 16));
  const placed = placeChartTooltip({
    pointX: 160,
    pointY: 80,
    containerWidth,
    containerHeight,
    tooltipWidth: 300,
    tooltipHeight: 210,
  });
  assert.ok(["above", "below", "left", "right"].includes(placed.side));
  if (placed.side === "right" || placed.side === "left") {
    assert.fail("narrow chart should stack the tooltip when it cannot sit beside the point");
  }
  assertInside(placed, containerWidth, containerHeight);
  assert.ok(placed.width <= 300);
  assert.ok(placed.width <= containerWidth - 16);
});

test("placement is recalculated when the chart container resizes", () => {
  const wide = placeChartTooltip({
    ...WIDE,
    pointX: plotPointX(47, 48, 900),
    pointY: 100,
    containerWidth: 900,
  });
  const resized = placeChartTooltip({
    pointX: plotPointX(47, 48, 360),
    pointY: 100,
    containerWidth: 360,
    containerHeight: 270,
    tooltipWidth: 300,
    tooltipHeight: 210,
  });
  assert.equal(wide.side, "left");
  assertInside(wide, 900);
  assertInside(resized, 360);
  assert.notEqual(wide.x, resized.x);
  assert.ok(resized.width <= 360 - 16);
  assert.match(chartSource, /ResizeObserver/);
  assert.match(chartSource, /placeChartTooltip/);
});

test("complete four-group tooltip content remains rendered", () => {
  assert.match(chartSource, />Forecast</);
  assert.match(chartSource, /Vs Overall Average/);
  assert.match(chartSource, /specificMonthAverageLabel/);
  assert.match(chartSource, /specificMonthChangeLabel/);
  assert.equal(calendarMonthShortName("2030-12"), "Dec");
  assert.equal(specificMonthAverageLabel("2030-12"), "Historical Dec Average");
  assert.equal(specificMonthChangeLabel("2030-12"), "Vs Dec Average");
  assert.doesNotMatch(chartSource, /Forecast vs Overall History/);
  assert.doesNotMatch(chartSource, /Overall Historical Average/);
  assert.doesNotMatch(chartSource, /EXPECTED_RANGE_LABEL/);
  const overall = formatTooltipChange(160.16, (160.16 / 653.92) * 100, "LKR_MILLIONS");
  assert.match(overall, /^LKR \+160\.16 Mn \(\+24\.49%\)$/);
});

test("tooltip overlay stays in the chart card and does not create page overflow", () => {
  const last = placeChartTooltip({ ...WIDE, pointX: plotPointX(47, 48), pointY: 40 });
  assertInside(last);
  assert.match(chartSource, /chart-area-tooltip-host/);
  assert.match(chartSource, /chart-tooltip-overlay-wrap/);
  assert.doesNotMatch(chartSource, /position:\s*["']fixed["']/);
  assert.doesNotMatch(chartSource, /allowEscapeViewBox/);
  assert.match(cssSource, /\.chart-area \{\s*position: relative;/);
  assert.match(cssSource, /\.chart-tooltip-overlay-wrap \{\s*position: absolute;/);
  assert.match(cssSource, /overflow: hidden;/);
  assert.doesNotMatch(cssSource, /\.chart-tooltip-overlay[^{]*\{[^}]*text-overflow:\s*ellipsis/);
  assert.match(cssSource, /min\(300px, calc\(100% - 16px\)\)/);
  assert.match(resultsSource, /amount: row\.forecast_amount/);
  assert.doesNotMatch(resultsSource, /forecast_amount\s*[+\-*/]/);
});

test("vertical placement clamps inside the chart when space above or below is tight", () => {
  const highPoint = placeChartTooltip({ ...WIDE, pointX: 400, pointY: 8 });
  const lowPoint = placeChartTooltip({ ...WIDE, pointX: 400, pointY: 260 });
  assert.equal(highPoint.y, 0);
  assert.equal(lowPoint.y, WIDE.containerHeight - highPoint.height);
  assertInside(highPoint);
  assertInside(lowPoint);
  assert.equal(DEFAULT_TOOLTIP_WIDTH, 280);
  assert.ok(DEFAULT_TOOLTIP_HEIGHT > 0);
});
