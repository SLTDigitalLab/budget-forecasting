import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  MONTH_OPTIONS,
  PERIOD_TYPE,
  alignEndWithStart,
  buildForecastRequest,
  compactPeriodHelp,
  defaultEndIso,
  financialYearOptions,
  firstCompleteFinancialYear,
  firstValidForecastIso,
  formatCompactPeriodRange,
  formatSelectedPeriodDisplay,
  isMonthDisabled,
  lastAllowedIso,
  remainingYearOptions,
  resolveForecastPeriod,
  toIsoMonth,
  validateForecastPeriod,
  validatePeriodTypeSelection,
  yearOptions,
} from "./forecastPeriod.js";

const controlsSource = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../components/ForecastControls.jsx"),
  "utf8"
);
const generatePageSource = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../pages/GenerateForecast.jsx"),
  "utf8"
);

test("dynamic year options start at the first valid forecast year", () => {
  const earliest = firstValidForecastIso("2026-06");
  assert.equal(earliest, "2026-07");
  const years = yearOptions(earliest);
  assert.equal(years[0], 2026);
  assert.ok(years.includes(2027));
  assert.ok(years.includes(2028));
  assert.equal(years[years.length - 1], 2030);
  assert.ok(!years.includes(2031));
});

test("year options follow a later history end instead of hardcoding 2026", () => {
  const years = yearOptions(firstValidForecastIso("2027-03"));
  assert.equal(years[0], 2027);
  assert.ok(years.includes(2028));
  assert.equal(years[years.length - 1], 2030);
});

test("2027 and 2028 remain selectable year options", () => {
  const years = yearOptions("2026-07");
  assert.deepEqual(
    years.filter((year) => year === 2027 || year === 2028),
    [2027, 2028]
  );
});

test("months before July 2026 are disabled in the first valid year", () => {
  const earliest = "2026-07";
  for (let month = 1; month <= 6; month += 1) {
    assert.equal(isMonthDisabled(2026, month, earliest), true, `month ${month}`);
  }
  for (let month = 7; month <= 12; month += 1) {
    assert.equal(isMonthDisabled(2026, month, earliest), false, `month ${month}`);
  }
});

test("all 12 months are selectable in 2027 and later", () => {
  const earliest = "2026-07";
  for (let month = 1; month <= 12; month += 1) {
    assert.equal(isMonthDisabled(2027, month, earliest), false);
    assert.equal(isMonthDisabled(2028, month, earliest), false);
  }
});

test("December 2030 is accepted and later months are disabled", () => {
  const earliest = firstValidForecastIso("2026-06");
  const latest = lastAllowedIso(earliest);
  assert.equal(earliest, "2026-07");
  assert.equal(latest, "2030-12");
  assert.equal(isMonthDisabled(2030, 12, earliest, latest), false);
  assert.equal(isMonthDisabled(2031, 1, earliest, latest), true);
  const message = validateForecastPeriod({
    startIso: "2031-01",
    endIso: "2031-01",
    earliestIso: earliest,
    historyEnd: "2026-06",
  });
  assert.match(message, /December 2030/);
  assert.equal(validateForecastPeriod({
    startIso: "2030-12",
    endIso: "2030-12",
    earliestIso: earliest,
    historyEnd: "2026-06",
  }), "");
  assert.ok(!remainingYearOptions(earliest).includes(2031));
});

test("month options convert January through December to two-digit values", () => {
  assert.equal(MONTH_OPTIONS.length, 12);
  assert.equal(MONTH_OPTIONS[0].label, "January");
  assert.equal(MONTH_OPTIONS[0].value, "01");
  assert.equal(MONTH_OPTIONS[11].label, "December");
  assert.equal(MONTH_OPTIONS[11].value, "12");
  assert.equal(toIsoMonth(2027, 1), "2027-01");
  assert.equal(toIsoMonth(2028, 12), "2028-12");
});

test("API request uses YYYY-MM start_month and end_month", () => {
  assert.deepEqual(buildForecastRequest(2027, "01", 2028, "12"), {
    start_month: "2027-01",
    end_month: "2028-12",
  });
  assert.deepEqual(buildForecastRequest(2026, 7, 2026, 12), {
    start_month: "2026-07",
    end_month: "2026-12",
  });
});

test("end period before start period is invalid", () => {
  const message = validateForecastPeriod({
    startIso: "2027-03",
    endIso: "2027-01",
    earliestIso: "2026-07",
    historyEnd: "2026-06",
  });
  assert.match(message, /end period cannot be before start period/i);
});

test("start period on or before history end is invalid", () => {
  const message = validateForecastPeriod({
    startIso: "2026-06",
    endIso: "2026-12",
    earliestIso: "2026-07",
    historyEnd: "2026-06",
  });
  assert.match(message, /after the model history end/i);
});

test("default period is July through December 2026 for current history end", () => {
  const start = firstValidForecastIso("2026-06");
  const end = defaultEndIso(start);
  assert.equal(start, "2026-07");
  assert.equal(end, "2026-12");
});

test("start after end automatically moves the end period to match", () => {
  assert.equal(alignEndWithStart("2027-01", "2026-12"), "2027-01");
  assert.equal(alignEndWithStart("2026-07", "2026-12"), "2026-12");
});

test("specific future month maps to the same start_month and end_month", () => {
  const resolved = resolveForecastPeriod({
    periodType: PERIOD_TYPE.SPECIFIC_MONTH,
    year: 2027,
    month: 3,
    earliestIso: "2026-07",
  });
  assert.deepEqual(resolved, { startIso: "2027-03", endIso: "2027-03" });
  assert.deepEqual(buildForecastRequest(2027, 3, 2027, 3), {
    start_month: "2027-03",
    end_month: "2027-03",
  });
});

test("remaining months of 2026 start after history end and finish in December", () => {
  const resolved = resolveForecastPeriod({
    periodType: PERIOD_TYPE.REMAINING_YEAR,
    year: 2026,
    earliestIso: "2026-07",
  });
  assert.deepEqual(resolved, { startIso: "2026-07", endIso: "2026-12" });
});

test("remaining months of a later year cover January through December", () => {
  const resolved = resolveForecastPeriod({
    periodType: PERIOD_TYPE.REMAINING_YEAR,
    year: 2027,
    earliestIso: "2026-07",
  });
  assert.deepEqual(resolved, { startIso: "2027-01", endIso: "2027-12" });
});

test("future financial years are complete January through December years", () => {
  assert.equal(firstCompleteFinancialYear("2026-07"), 2027);
  const resolved = resolveForecastPeriod({
    periodType: PERIOD_TYPE.FINANCIAL_YEARS,
    startYear: 2027,
    endYear: 2028,
    earliestIso: "2026-07",
  });
  assert.deepEqual(resolved, { startIso: "2027-01", endIso: "2028-12" });
  assert.deepEqual(buildForecastRequest(2027, 1, 2028, 12), {
    start_month: "2027-01",
    end_month: "2028-12",
  });
});

test("the current partial year is not offered as a complete financial year", () => {
  const years = financialYearOptions("2026-07");
  assert.equal(years[0], 2027);
  assert.ok(!years.includes(2026));
  const message = validatePeriodTypeSelection({
    periodType: PERIOD_TYPE.FINANCIAL_YEARS,
    startIso: "2026-07",
    endIso: "2026-12",
    earliestIso: "2026-07",
    historyEnd: "2026-06",
  });
  assert.match(message, /complete January–December/i);
});

test("forecast period type is required", () => {
  const message = validatePeriodTypeSelection({
    periodType: "",
    startIso: "2026-07",
    endIso: "2026-12",
    earliestIso: "2026-07",
    historyEnd: "2026-06",
  });
  assert.match(message, /forecast period type is required/i);
});

test("compact period help is derived from the selected range and history end", () => {
  assert.equal(formatCompactPeriodRange("2027-01", "2027-12"), "Jan–Dec 2027");
  assert.equal(formatCompactPeriodRange("2026-08", "2026-08"), "Aug 2026");
  assert.equal(formatSelectedPeriodDisplay("2027-01", "2027-12"), "Jan 2027 – Dec 2027");
  assert.equal(
    compactPeriodHelp({ startIso: "2027-01", endIso: "2027-12", earliestIso: "2026-07" }),
    "Forecasts Jan–Dec 2027 · Available from Jul 2026"
  );
  assert.equal(
    compactPeriodHelp({ startIso: "2026-07", endIso: "2026-12", earliestIso: "2026-07" }),
    "Forecasts Jul–Dec 2026 · Available from Jul 2026"
  );
});

test("generate forecast form no longer repeats the page description", () => {
  assert.match(generatePageSource, /Select a budget category and forecast period to generate predictions/);
  assert.doesNotMatch(controlsSource, /generate-form-lead/);
  assert.doesNotMatch(controlsSource, /Select a budget category and forecast period to generate predictions/);
  assert.match(controlsSource, /generate-period-action-row/);
  assert.match(controlsSource, /Forecast Year/);
  assert.match(controlsSource, /start_month|startMonth/);
});

test("API request format stays start_month and end_month for every period type", () => {
  const specific = resolveForecastPeriod({
    periodType: PERIOD_TYPE.SPECIFIC_MONTH,
    year: 2026,
    month: 8,
    earliestIso: "2026-07",
  });
  const remaining = resolveForecastPeriod({
    periodType: PERIOD_TYPE.REMAINING_YEAR,
    year: 2026,
    earliestIso: "2026-07",
  });
  const financial = resolveForecastPeriod({
    periodType: PERIOD_TYPE.FINANCIAL_YEARS,
    startYear: 2027,
    endYear: 2027,
    earliestIso: "2026-07",
  });
  assert.deepEqual(buildForecastRequest(...specific.startIso.split("-"), ...specific.endIso.split("-")), {
    start_month: "2026-08",
    end_month: "2026-08",
  });
  assert.equal(remaining.startIso, "2026-07");
  assert.equal(remaining.endIso, "2026-12");
  assert.equal(financial.startIso, "2027-01");
  assert.equal(financial.endIso, "2027-12");
});
