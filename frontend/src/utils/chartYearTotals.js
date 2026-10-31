const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

import { resolveHistoricalDisplayWindow } from "./historicalDisplayWindow.js";

export const YEAR_TOTAL_KIND = "year_total";
export const YEAR_TOTAL_FILL = "#EF4444";
export const YEAR_TOTAL_FILL_DARK = "#F87171";

function isIsoMonth(value) {
  return /^\d{4}-\d{2}$/.test(String(value || ""));
}

function parseIso(iso) {
  if (!isIsoMonth(iso)) {
    return null;
  }
  const [yearText, monthText] = String(iso).split("-");
  return { year: Number(yearText), month: Number(monthText), iso: String(iso) };
}

function monthName(monthNumber) {
  return MONTH_SHORT[monthNumber - 1] || String(monthNumber);
}

function finiteOrNull(value) {
  if (value == null || value === "") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function resolveRowIso(row, isoKey) {
  if (isoKey && row?.[isoKey]) {
    return String(row[isoKey]);
  }
  if (isIsoMonth(row?.isoMonth)) {
    return String(row.isoMonth);
  }
  if (isIsoMonth(row?.month)) {
    return String(row.month);
  }
  return "";
}

/**
 * Sum unrounded monthly values. Missing entries are skipped (not treated as zero).
 * Returns null when every month is missing/unavailable.
 */
export function sumAvailableAmounts(values) {
  const finite = (values || []).map(finiteOrNull).filter((value) => value != null);
  if (!finite.length) {
    return null;
  }
  return finite.reduce((sum, value) => sum + value, 0);
}

/**
 * Label rules for a year summary bar:
 * - Starts in January → "[year] YTD Value" (+ partial note when not Dec)
 * - Starts after January → "Period Total" with the actual range
 */
export function yearTotalLabel({ year, startMonth, endMonth }) {
  const start = Number(startMonth);
  const end = Number(endMonth);
  const y = Number(year);
  if (!Number.isFinite(y) || !start || !end) {
    return "Period Total";
  }
  if (start === 1) {
    const base = `${y} YTD Value`;
    if (end < 12) {
      return `${base} (Partial: Jan–${monthName(end)})`;
    }
    return base;
  }
  return "Period Total";
}

/** True when the selected forecast months for a year are exactly Jan–Dec (12 months). */
export function isFullForecastCalendarYear({ startMonth, endMonth, monthCount }) {
  return Number(startMonth) === 1 && Number(endMonth) === 12 && Number(monthCount) === 12;
}

/**
 * Combined Monthly Forecast side-chart titles:
 * - Full Jan–Dec year → "YTD YYYY"
 * - Any other selection → "Forecast Period Total YYYY"
 */
export function forecastYearChartTitle({ year, startMonth, endMonth, monthCount }) {
  const y = Number(year);
  if (!Number.isFinite(y)) {
    return "Forecast Period Total";
  }
  if (isFullForecastCalendarYear({ startMonth, endMonth, monthCount })) {
    return `YTD ${y}`;
  }
  return `Forecast Period Total ${y}`;
}

/** Human-readable range for the selected forecast months within one calendar year. */
export function forecastYearRangeLabel({ year, startMonth, endMonth }) {
  const y = Number(year);
  const start = Number(startMonth);
  const end = Number(endMonth);
  if (!Number.isFinite(y) || !start || !end) {
    return "";
  }
  if (start === end) {
    return `${monthName(start)} ${y}`;
  }
  return `${monthName(start)} ${y} – ${monthName(end)} ${y}`;
}

/**
 * Shared Y domain for Combined Monthly year-total charts.
 * Independent of the monthly chart. Nonnegative totals start at 0;
 * negative headroom is added only when a yearly total is negative.
 */
export function sharedYearTotalDomain(totals, { paddingRatio = 0.12 } = {}) {
  const finite = (totals || [])
    .map((value) => (value == null || value === "" ? null : Number(value)))
    .filter((value) => Number.isFinite(value));
  if (!finite.length) {
    return [0, 1];
  }
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const high = Math.max(max, 0);
  const low = min < 0 ? min : 0;
  const span = high - low;
  const padding = span < 1e-9 ? Math.max(Math.abs(high || min) * paddingRatio, 1) : span * paddingRatio;
  return [low < 0 ? low - padding : 0, high + padding];
}

export function yearTotalTooltipTitle({ year, startMonth, endMonth, seriesKind = "forecast" }) {
  const start = Number(startMonth);
  const end = Number(endMonth);
  const y = Number(year);
  const kindLabel = seriesKind === "actual" ? "Actual" : "Forecast";
  if (!Number.isFinite(y) || !start || !end) {
    return `${kindLabel} period total`;
  }
  if (start === 1) {
    return `${kindLabel} YTD · Jan–${monthName(end)} ${y}`;
  }
  return `${kindLabel} period · ${monthName(start)}–${monthName(end)} ${y}`;
}

function axisLabelForTotal(meta) {
  if (meta.startMonth === 1) {
    return meta.endMonth < 12 ? `${meta.year} YTD*` : `${meta.year} YTD`;
  }
  return "Period Total";
}

function buildSummaryRow({
  year,
  startMonth,
  endMonth,
  startIso,
  endIso,
  seriesKind,
  total,
  availableCount,
  monthCount,
  labelKey,
  valueKey,
}) {
  const meta = { year, startMonth, endMonth, seriesKind };
  const axis = axisLabelForTotal(meta);
  return {
    isYearTotal: true,
    yearTotalKind: YEAR_TOTAL_KIND,
    seriesKind,
    year,
    startMonth,
    endMonth,
    startIso,
    endIso,
    availableCount,
    monthCount,
    totalUnavailable: total == null,
    [labelKey]: axis,
    label: axis,
    month: `${year}-YTD-${seriesKind}`,
    isoMonth: null,
    displayLabel: yearTotalLabel(meta),
    tooltipTitle: yearTotalTooltipTitle(meta),
    amount: valueKey === "amount" || !valueKey ? total : null,
    actual: seriesKind === "actual" ? total : null,
    forecast: seriesKind === "forecast" ? total : null,
    [valueKey || "amount"]: total,
    lower: null,
    upper: null,
    intervalError: null,
    interval_status: null,
    intervalStatus: null,
    barGapBefore: true,
  };
}

/**
 * Insert solid red year-summary bars after each calendar year's monthly rows.
 * Presentation-only — never feed these back into KPIs, exports, or APIs.
 */
export function appendYearTotalBars(rows, {
  isoKey,
  valueKey = "amount",
  seriesKind = "forecast",
  labelKey = "label",
} = {}) {
  const source = Array.isArray(rows) ? rows.filter((row) => !row?.isYearTotal) : [];
  if (!source.length) {
    return [];
  }

  const byYear = new Map();
  source.forEach((row) => {
    const parsed = parseIso(resolveRowIso(row, isoKey));
    if (!parsed) {
      return;
    }
    if (!byYear.has(parsed.year)) {
      byYear.set(parsed.year, []);
    }
    byYear.get(parsed.year).push({ row, parsed });
  });

  const result = [];
  source.forEach((row) => {
    result.push(row);
    const parsed = parseIso(resolveRowIso(row, isoKey));
    if (!parsed) {
      return;
    }
    const group = byYear.get(parsed.year);
    const last = group?.[group.length - 1];
    if (!last || last.parsed.iso !== parsed.iso) {
      return;
    }
    const amounts = group.map((entry) => finiteOrNull(entry.row[valueKey]));
    const total = sumAvailableAmounts(amounts);
    result.push(
      buildSummaryRow({
        year: parsed.year,
        startMonth: group[0].parsed.month,
        endMonth: group[group.length - 1].parsed.month,
        startIso: group[0].parsed.iso,
        endIso: group[group.length - 1].parsed.iso,
        seriesKind,
        total,
        availableCount: amounts.filter((value) => value != null).length,
        monthCount: group.length,
        labelKey,
        valueKey,
      })
    );
  });
  return result;
}

/**
 * When a timeline mixes actual and forecast months, insert separate summary bars
 * per series (never silently combine them).
 */
export function appendMixedYearTotalBars(rows, {
  isoKey,
  actualKey = "actual",
  forecastKey = "forecast",
  labelKey = "label",
} = {}) {
  const source = Array.isArray(rows) ? rows.filter((row) => !row?.isYearTotal) : [];
  if (!source.length) {
    return [];
  }

  const byYear = new Map();
  source.forEach((row) => {
    const parsed = parseIso(resolveRowIso(row, isoKey));
    if (!parsed) {
      return;
    }
    if (!byYear.has(parsed.year)) {
      byYear.set(parsed.year, []);
    }
    byYear.get(parsed.year).push({ row, parsed });
  });

  const result = [];
  source.forEach((row) => {
    result.push(row);
    const parsed = parseIso(resolveRowIso(row, isoKey));
    if (!parsed) {
      return;
    }
    const group = byYear.get(parsed.year);
    const last = group?.[group.length - 1];
    if (!last || last.parsed.iso !== parsed.iso) {
      return;
    }

    const actualEntries = group.filter((entry) => finiteOrNull(entry.row[actualKey]) != null);
    const forecastEntries = group.filter((entry) => finiteOrNull(entry.row[forecastKey]) != null);

    if (actualEntries.length) {
      const amounts = actualEntries.map((entry) => finiteOrNull(entry.row[actualKey]));
      result.push(
        buildSummaryRow({
          year: parsed.year,
          startMonth: actualEntries[0].parsed.month,
          endMonth: actualEntries[actualEntries.length - 1].parsed.month,
          startIso: actualEntries[0].parsed.iso,
          endIso: actualEntries[actualEntries.length - 1].parsed.iso,
          seriesKind: "actual",
          total: sumAvailableAmounts(amounts),
          availableCount: amounts.filter((value) => value != null).length,
          monthCount: actualEntries.length,
          labelKey,
          valueKey: actualKey,
        })
      );
    }
    if (forecastEntries.length) {
      const amounts = forecastEntries.map((entry) => finiteOrNull(entry.row[forecastKey]));
      result.push(
        buildSummaryRow({
          year: parsed.year,
          startMonth: forecastEntries[0].parsed.month,
          endMonth: forecastEntries[forecastEntries.length - 1].parsed.month,
          startIso: forecastEntries[0].parsed.iso,
          endIso: forecastEntries[forecastEntries.length - 1].parsed.iso,
          seriesKind: "forecast",
          total: sumAvailableAmounts(amounts),
          availableCount: amounts.filter((value) => value != null).length,
          monthCount: forecastEntries.length,
          labelKey,
          valueKey: forecastKey,
        })
      );
    }
  });
  return result;
}

/**
 * Latest-2-years view for mixed actual/forecast timelines.
 * Uses the two latest complete calendar years from confirmed actual month rows,
 * then keeps every forecast month (and any existing gap rows between them).
 * Display-window only — does not truncate stored history or change forecast values.
 */
export function filterRecentHistoryWithForecast(points, {
  actualKey = "actual",
  forecastKey = "forecast",
  isoKey,
  historyStartIso,
  confirmedMonthIsos = null,
} = {}) {
  const source = Array.isArray(points) ? points.filter((row) => !isYearTotalRow(row)) : [];
  if (!source.length) {
    return [];
  }

  let latestActual = null;
  let earliestActual = null;
  let firstForecast = null;
  const actualMonthIsos = [];
  source.forEach((row) => {
    const iso = resolveRowIso(row, isoKey);
    if (!isIsoMonth(iso)) {
      return;
    }
    if (finiteOrNull(row?.[forecastKey]) != null) {
      if (!firstForecast || iso < firstForecast) {
        firstForecast = iso;
      }
      return;
    }
    // Historical side only — ignore forecast rows and do not invent completeness from them.
    actualMonthIsos.push(iso);
    if (!latestActual || iso > latestActual) {
      latestActual = iso;
    }
    if (!earliestActual || iso < earliestActual) {
      earliestActual = iso;
    }
  });

  const rangeStart = historyStartIso || earliestActual || null;
  const confirmedMonths = (Array.isArray(confirmedMonthIsos) ? confirmedMonthIsos : [])
    .map((value) => String(value || ""))
    .filter(isIsoMonth);
  const window = resolveHistoricalDisplayWindow(
    latestActual,
    rangeStart,
    undefined,
    confirmedMonths.length ? confirmedMonths : actualMonthIsos
  );
  if (!window) {
    // No complete calendar year — keep forecast months only.
    return source.filter((row) => finiteOrNull(row?.[forecastKey]) != null);
  }

  return source.filter((row) => {
    const iso = resolveRowIso(row, isoKey);
    if (finiteOrNull(row?.[forecastKey]) != null) {
      return true;
    }
    if (!isIsoMonth(iso)) {
      return true;
    }
    if (iso >= window.startIso && iso <= window.endIso) {
      return true;
    }
    // Preserve blank gap placeholders between the historical window and forecast start.
    // Do not pull later partial-year actuals into Latest 2 years.
    if (
      firstForecast
      && iso > window.endIso
      && iso < firstForecast
      && finiteOrNull(row?.[actualKey]) == null
    ) {
      return true;
    }
    return false;
  });
}

/** @deprecated Prefer filterRecentHistoryWithForecast — kept as an alias for callers. */
export function filterRecentActualYears(points, options = {}) {
  return filterRecentHistoryWithForecast(points, options);
}

export function isYearTotalRow(row) {
  return Boolean(row?.isYearTotal) || row?.yearTotalKind === YEAR_TOTAL_KIND;
}

function buildYearSummaryCard({
  year,
  startMonth,
  endMonth,
  startIso,
  endIso,
  total,
  availableCount,
  monthCount,
  seriesKind,
}) {
  const isFullCalendar = startMonth === 1 && endMonth === 12 && monthCount === 12;
  const hasGaps = availableCount < monthCount;
  const isForecast = seriesKind === "forecast";

  let title;
  if (isForecast) {
    if (startMonth === 1 && endMonth === 12) {
      title = `Forecast Total · Jan–Dec ${year}`;
    } else if (startMonth === 1) {
      title = `Forecast Total · Jan–${monthName(endMonth)} ${year}`;
    } else {
      title = `Forecast Total · ${monthName(startMonth)}–${monthName(endMonth)} ${year}`;
    }
  } else if (isFullCalendar) {
    title = `Historical Annual Total · ${year}`;
  } else if (startMonth === 1) {
    title = `Historical YTD · Jan–${monthName(endMonth)} ${year}`;
  } else {
    title = `Historical Period Total · ${monthName(startMonth)}–${monthName(endMonth)} ${year}`;
  }

  let coverageLabel = null;
  if (hasGaps) {
    coverageLabel = `${availableCount} of ${monthCount} months with values`;
  } else if (!isFullCalendar && startMonth === 1 && endMonth < 12) {
    coverageLabel = `Partial year · through ${monthName(endMonth)}`;
  }

  return {
    id: `${seriesKind}-${year}-${startIso}-${endIso}`,
    year,
    startMonth,
    endMonth,
    startIso,
    endIso,
    total,
    availableCount,
    monthCount,
    isFullCalendar,
    hasGaps,
    seriesKind: isForecast ? "forecast" : "actual",
    title,
    coverageLabel,
  };
}

function summarizeOneSeries(source, { isoKey, valueKey, seriesKind }) {
  const byYear = new Map();
  source.forEach((row) => {
    const parsed = parseIso(resolveRowIso(row, isoKey));
    if (!parsed) {
      return;
    }
    if (!byYear.has(parsed.year)) {
      byYear.set(parsed.year, []);
    }
    byYear.get(parsed.year).push({ row, parsed });
  });

  return [...byYear.keys()]
    .sort((left, right) => left - right)
    .map((year) => {
      const group = byYear.get(year).slice().sort((a, b) => a.parsed.iso.localeCompare(b.parsed.iso));
      const valued = group.filter((entry) => finiteOrNull(entry.row[valueKey]) != null);
      if (!valued.length) {
        return null;
      }
      const firstIso = valued[0].parsed.iso;
      const lastIso = valued[valued.length - 1].parsed.iso;
      const windowed = group.filter((entry) => entry.parsed.iso >= firstIso && entry.parsed.iso <= lastIso);
      const amounts = windowed.map((entry) => finiteOrNull(entry.row[valueKey]));
      const availableCount = amounts.filter((value) => value != null).length;
      return buildYearSummaryCard({
        year,
        startMonth: windowed[0].parsed.month,
        endMonth: windowed[windowed.length - 1].parsed.month,
        startIso: windowed[0].parsed.iso,
        endIso: windowed[windowed.length - 1].parsed.iso,
        total: sumAvailableAmounts(amounts),
        availableCount,
        monthCount: windowed.length,
        seriesKind,
      });
    })
    .filter(Boolean);
}

/**
 * Compact card summaries for every year in a displayed monthly series.
 * Uses monthly observations only (never summary/YTD rows). Presentation-only.
 */
export function summarizeDisplayedYears(rows, {
  isoKey,
  valueKey = "actual",
  seriesKind = "actual",
} = {}) {
  const source = (Array.isArray(rows) ? rows : []).filter((row) => !isYearTotalRow(row));
  return summarizeOneSeries(source, { isoKey, valueKey, seriesKind });
}

/**
 * Mixed actual/forecast timeline: separate historical and forecast year cards.
 */
export function summarizeMixedDisplayedYears(rows, {
  isoKey,
  actualKey = "actual",
  forecastKey = "forecast",
} = {}) {
  const source = (Array.isArray(rows) ? rows : []).filter((row) => !isYearTotalRow(row));
  const actuals = summarizeOneSeries(source, { isoKey, valueKey: actualKey, seriesKind: "actual" });
  const forecasts = summarizeOneSeries(source, { isoKey, valueKey: forecastKey, seriesKind: "forecast" });
  return [...actuals, ...forecasts].sort((left, right) => {
    if (left.year !== right.year) {
      return left.year - right.year;
    }
    if (left.seriesKind === right.seriesKind) {
      return 0;
    }
    return left.seriesKind === "actual" ? -1 : 1;
  });
}

/**
 * Compact card summary for the latest year in a displayed monthly series.
 * Uses monthly observations only (never summary/YTD rows). Presentation-only.
 */
export function summarizeLatestDisplayedYear(rows, options = {}) {
  const cards = summarizeDisplayedYears(rows, {
    isoKey: options.isoKey,
    valueKey: options.valueKey || "actual",
    seriesKind: options.seriesKind || "actual",
  });
  if (!cards.length) {
    return null;
  }
  const latest = cards[cards.length - 1];
  // Keep the short titles used by Historical Expenditure trend.
  let title = latest.title;
  if (latest.seriesKind === "actual") {
    if (latest.isFullCalendar) {
      title = `Annual Total · ${latest.year}`;
    } else if (latest.startMonth === 1) {
      title = `YTD · Jan–${monthName(latest.endMonth)} ${latest.year}`;
    } else {
      title = `Period Total · ${monthName(latest.startMonth)}–${monthName(latest.endMonth)} ${latest.year}`;
    }
  }
  return { ...latest, title };
}

export function chartPlotWidth(rowCount, barWidth = 28, gap = 12) {
  const count = Math.max(0, Number(rowCount) || 0);
  return Math.max(360, count * (barWidth + gap) + 96);
}

export function yearTotalFill(isDark) {
  if (typeof document !== "undefined") {
    const token = getComputedStyle(document.documentElement).getPropertyValue("--chart-ytd").trim();
    if (token) {
      return token;
    }
  }
  return isDark ? YEAR_TOTAL_FILL_DARK : YEAR_TOTAL_FILL;
}
