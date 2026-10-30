import { addMonths } from "./months.js";

const MONTH_LABELS = [
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

/** Latest two complete calendar years in confirmed historical month records. */
export const HISTORY_RANGE_LATEST_2_YEARS = "latest2";
/** @deprecated Use HISTORY_RANGE_LATEST_2_YEARS */
export const HISTORY_RANGE_LAST_24 = HISTORY_RANGE_LATEST_2_YEARS;
export const HISTORY_RANGE_ALL = "all";

function isIsoMonth(value) {
  return /^\d{4}-\d{2}$/.test(String(value || ""));
}

function parseIsoMonth(value) {
  if (!isIsoMonth(value)) {
    return null;
  }
  const [yearText, monthText] = String(value).split("-");
  return { year: Number(yearText), month: Number(monthText), iso: String(value) };
}

function longMonthLabel(isoMonth) {
  const parsed = parseIsoMonth(isoMonth);
  if (!parsed) {
    return isoMonth || "";
  }
  return `${MONTH_LABELS[parsed.month - 1]} ${parsed.year}`;
}

function finiteAmount(value) {
  if (value == null || value === "") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function monthIndex(year, month) {
  return year * 12 + (month - 1);
}

function normalizeRangeMode(rangeMode) {
  if (rangeMode === HISTORY_RANGE_ALL || rangeMode === "all") {
    return HISTORY_RANGE_ALL;
  }
  // Accept legacy last24 / recent aliases as latest-2-years.
  return HISTORY_RANGE_LATEST_2_YEARS;
}

/**
 * Complete calendar years from confirmed month records.
 * A year is complete when Jan–Dec month keys exist. Amounts may be null
 * (explicit missing); absent month keys make the year incomplete.
 */
export function listCompleteCalendarYears(monthIsos) {
  const byYear = new Map();
  (Array.isArray(monthIsos) ? monthIsos : []).forEach((value) => {
    const parsed = parseIsoMonth(value);
    if (!parsed) {
      return;
    }
    if (!byYear.has(parsed.year)) {
      byYear.set(parsed.year, new Set());
    }
    byYear.get(parsed.year).add(parsed.month);
  });
  return [...byYear.entries()]
    .filter(([, months]) => {
      if (months.size !== 12) {
        return false;
      }
      for (let month = 1; month <= 12; month += 1) {
        if (!months.has(month)) {
          return false;
        }
      }
      return true;
    })
    .map(([year]) => year)
    .sort((left, right) => left - right);
}

function monthIsosFromHistory(history) {
  const fromRows = (Array.isArray(history?.monthly_actuals) ? history.monthly_actuals : [])
    .map((row) => String(row?.month || ""))
    .filter(isIsoMonth);
  if (fromRows.length) {
    return fromRows;
  }
  const start = parseIsoMonth(history?.history_start);
  const end = parseIsoMonth(history?.latest_actual_month || history?.history_end);
  if (!start || !end || start.iso > end.iso) {
    return [];
  }
  const months = [];
  let cursor = start.iso;
  while (cursor <= end.iso) {
    months.push(cursor);
    cursor = addMonths(cursor, 1);
  }
  return months;
}

/**
 * Latest two complete calendar years.
 * Example: data through Sep 2026 → Jan 2024–Dec 2025.
 * When 2026 is complete → Jan 2025–Dec 2026.
 * Returns null when no complete year exists (empty state).
 */
export function resolveLatestTwoCompleteYearsWindow(monthIsos) {
  const completeYears = listCompleteCalendarYears(monthIsos);
  if (!completeYears.length) {
    return null;
  }
  const selectedYears = completeYears.slice(-2);
  const startYear = selectedYears[0];
  const endYear = selectedYears[selectedYears.length - 1];
  return {
    startIso: `${startYear}-01`,
    endIso: `${endYear}-12`,
    startYear,
    endYear,
    endMonth: 12,
    selectedYears,
    mode: HISTORY_RANGE_LATEST_2_YEARS,
  };
}

/**
 * Full available confirmed history, including partial years.
 */
export function resolveAllHistoryWindow(latestIsoMonth, historyStartIso) {
  const latest = parseIsoMonth(latestIsoMonth);
  if (!latest) {
    return null;
  }
  const availableStart = parseIsoMonth(historyStartIso);
  const startIso = availableStart && availableStart.iso <= latest.iso ? availableStart.iso : latest.iso;
  return {
    startIso,
    endIso: latest.iso,
    startYear: parseIsoMonth(startIso).year,
    endYear: latest.year,
    endMonth: latest.month,
    selectedYears: null,
    mode: HISTORY_RANGE_ALL,
  };
}

/**
 * @deprecated Rolling 24-month helper retained for older imports; prefer resolveLatestTwoCompleteYearsWindow.
 */
export function resolveLast24MonthsWindow(latestIsoMonth, historyStartIso) {
  const latest = parseIsoMonth(latestIsoMonth);
  if (!latest) {
    return null;
  }
  let startIso = addMonths(latest.iso, -23);
  const availableStart = parseIsoMonth(historyStartIso);
  if (availableStart && startIso < availableStart.iso) {
    startIso = availableStart.iso;
  }
  if (startIso > latest.iso) {
    startIso = latest.iso;
  }
  return {
    startIso,
    endIso: latest.iso,
    startYear: parseIsoMonth(startIso).year,
    endYear: latest.year,
    endMonth: latest.month,
    selectedYears: null,
    mode: HISTORY_RANGE_LATEST_2_YEARS,
  };
}

/**
 * Resolve a historical display window from confirmed month records.
 */
export function resolveHistoricalDisplayWindow(
  latestIsoMonth,
  historyStartIso,
  rangeMode = HISTORY_RANGE_LATEST_2_YEARS,
  monthIsos = null
) {
  const mode = normalizeRangeMode(rangeMode);
  if (mode === HISTORY_RANGE_ALL) {
    return resolveAllHistoryWindow(latestIsoMonth, historyStartIso);
  }
  const months = Array.isArray(monthIsos) && monthIsos.length
    ? monthIsos
    : (() => {
      const start = parseIsoMonth(historyStartIso);
      const end = parseIsoMonth(latestIsoMonth);
      if (!start || !end || start.iso > end.iso) {
        return [];
      }
      const list = [];
      let cursor = start.iso;
      while (cursor <= end.iso) {
        list.push(cursor);
        cursor = addMonths(cursor, 1);
      }
      return list;
    })();
  return resolveLatestTwoCompleteYearsWindow(months);
}

export function countCalendarMonthsInWindow(window) {
  if (!window?.startIso || !window?.endIso) {
    return 0;
  }
  const start = parseIsoMonth(window.startIso);
  const end = parseIsoMonth(window.endIso);
  if (!start || !end) {
    return 0;
  }
  return monthIndex(end.year, end.month) - monthIndex(start.year, start.month) + 1;
}

export function formatHistoricalPeriodLabel(window) {
  if (!window?.startIso || !window?.endIso) {
    return "Historical period: unavailable";
  }
  return `Historical period: ${longMonthLabel(window.startIso)} – ${longMonthLabel(window.endIso)}`;
}

export function formatDisplayedRangeLabel(window) {
  if (!window?.startIso || !window?.endIso) {
    return "Displayed range: unavailable";
  }
  return `Displayed range: ${longMonthLabel(window.startIso)} – ${longMonthLabel(window.endIso)}`;
}

export function filterMonthlyActualsToWindow(monthlyActuals, window) {
  const rows = Array.isArray(monthlyActuals) ? monthlyActuals : [];
  if (!window?.startIso || !window?.endIso) {
    return rows.slice();
  }
  return rows.filter((row) => {
    const iso = String(row?.month || "");
    return isIsoMonth(iso) && iso >= window.startIso && iso <= window.endIso;
  });
}

function likeForLikeGrowth(current, previous) {
  if (!previous) {
    return null;
  }
  const shared = Object.keys(current)
    .map(Number)
    .filter((month) => Object.prototype.hasOwnProperty.call(previous, month))
    .sort((left, right) => left - right);
  if (!shared.length) {
    return null;
  }
  const currentTotal = shared.reduce((sum, month) => sum + current[month], 0);
  const previousTotal = shared.reduce((sum, month) => sum + previous[month], 0);
  if (previousTotal === 0 || !Number.isFinite(currentTotal) || !Number.isFinite(previousTotal)) {
    return null;
  }
  return ((currentTotal - previousTotal) / previousTotal) * 100;
}

function buildYearMaps(completeTotals) {
  const byYear = new Map();
  completeTotals.forEach(([iso, amount]) => {
    const parsed = parseIsoMonth(iso);
    if (!parsed || amount == null) {
      return;
    }
    if (!byYear.has(parsed.year)) {
      byYear.set(parsed.year, {});
    }
    byYear.get(parsed.year)[parsed.month] = amount;
  });
  return byYear;
}

/**
 * Yearly rows for the display window. Prior-year months may be supplied for
 * matching-month YoY only and are omitted from the returned series.
 */
export function buildWindowedYearlyActuals(displayCompleteTotals, baselineCompleteTotals = []) {
  const displayYears = new Set(
    displayCompleteTotals
      .map(([iso]) => parseIsoMonth(iso)?.year)
      .filter((year) => Number.isFinite(year))
  );
  const byYear = buildYearMaps([...baselineCompleteTotals, ...displayCompleteTotals]);
  return [...displayYears].sort((left, right) => left - right).map((year) => {
    const monthsMap = byYear.get(year) || {};
    const monthsIncluded = Object.keys(monthsMap).length;
    return {
      year,
      actual_amount: Object.values(monthsMap).reduce((sum, value) => sum + value, 0),
      months_included: monthsIncluded,
      year_status: monthsIncluded === 12 ? "FULL_YEAR" : "PARTIAL_YEAR",
      growth_percent: likeForLikeGrowth(monthsMap, byYear.get(year - 1) || null),
    };
  });
}

export function buildWindowedSeasonalProfile(displayCompleteTotals) {
  const buckets = Array.from({ length: 12 }, () => []);
  displayCompleteTotals.forEach(([iso, amount]) => {
    const parsed = parseIsoMonth(iso);
    if (!parsed || amount == null) {
      return;
    }
    buckets[parsed.month - 1].push(amount);
  });
  return buckets.map((values, index) => ({
    calendar_month: index + 1,
    month_label: MONTH_LABELS[index],
    average_amount: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null,
    observation_count: values.length,
  }));
}

function completeTotalsFromMonthly(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => {
      const amount = finiteAmount(row?.actual_amount ?? row?.actual);
      if (!isIsoMonth(row?.month) || amount == null) {
        return null;
      }
      return [String(row.month), amount];
    })
    .filter(Boolean);
}

function amountRowsForAggregates(displayMonths, window) {
  const selected = Array.isArray(window?.selectedYears) ? new Set(window.selectedYears) : null;
  if (!selected) {
    return displayMonths;
  }
  // Latest 2 years: exclude incomplete intervening years from aggregates.
  return displayMonths.filter((row) => {
    const parsed = parseIsoMonth(row?.month);
    return parsed && selected.has(parsed.year);
  });
}

/**
 * Build Historical Budget Analysis metrics for the selected display range.
 * Baseline months before the window are used only for YoY matching-month growth.
 */
export function buildHistoricalDisplayAnalysis(history, { rangeMode = HISTORY_RANGE_LATEST_2_YEARS } = {}) {
  if (!history) {
    return null;
  }
  const mode = normalizeRangeMode(rangeMode);
  const allMonths = Array.isArray(history.monthly_actuals) ? history.monthly_actuals : [];
  const monthIsos = monthIsosFromHistory(history);
  const latestIso =
    history.latest_actual_month
    || history.history_end
    || (monthIsos.length ? monthIsos[monthIsos.length - 1] : null);
  const window = resolveHistoricalDisplayWindow(
    latestIso,
    history.history_start,
    mode,
    monthIsos
  );
  if (!window) {
    return {
      window: null,
      rangeMode: mode,
      periodLabel: "Historical period: unavailable",
      displayedRangeLabel: "Displayed range: unavailable",
      calendarMonthCount: 0,
      monthly_actuals: [],
      overall_total: 0,
      monthly_average: null,
      complete_month_count: 0,
      yearly_actuals: [],
      seasonal_profile: buildWindowedSeasonalProfile([]),
      latest_year_growth_percent: null,
      budget_codes: Array.isArray(history.budget_codes) ? history.budget_codes : [],
      recurring_count: history.recurring_count,
      budget_code_count: history.budget_code_count,
      emptyReason: "No complete calendar year is available in confirmed historical data.",
    };
  }

  const displayMonths = filterMonthlyActualsToWindow(allMonths, window);
  const aggregateMonths = amountRowsForAggregates(displayMonths, window);
  const displayComplete = completeTotalsFromMonthly(aggregateMonths);
  const baselineYear = window.startYear - 1;
  const baselineComplete = completeTotalsFromMonthly(
    allMonths.filter((row) => {
      const parsed = parseIsoMonth(row?.month);
      return parsed && parsed.year === baselineYear;
    })
  );

  const overallTotal = displayComplete.reduce((sum, [, amount]) => sum + amount, 0);
  const completeMonthCount = displayComplete.length;
  const monthlyAverage = completeMonthCount ? overallTotal / completeMonthCount : null;
  const yearlyActuals = buildWindowedYearlyActuals(displayComplete, baselineComplete);
  const seasonalProfile = buildWindowedSeasonalProfile(displayComplete);
  const latestYearGrowthPercent = yearlyActuals.length
    ? yearlyActuals[yearlyActuals.length - 1].growth_percent
    : null;

  return {
    window,
    rangeMode: window.mode || mode,
    periodLabel: formatHistoricalPeriodLabel(window),
    displayedRangeLabel: formatDisplayedRangeLabel(window),
    calendarMonthCount: countCalendarMonthsInWindow(window),
    monthly_actuals: displayMonths,
    overall_total: overallTotal,
    monthly_average: monthlyAverage,
    complete_month_count: completeMonthCount,
    yearly_actuals: yearlyActuals,
    seasonal_profile: seasonalProfile,
    latest_year_growth_percent: latestYearGrowthPercent,
    // Rankings are refreshed from the analysis API for the active range.
    budget_codes: Array.isArray(history.budget_codes) ? history.budget_codes : [],
    recurring_count: history.recurring_count,
    budget_code_count: history.budget_code_count,
    emptyReason: null,
  };
}
