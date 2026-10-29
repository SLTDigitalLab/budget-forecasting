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

export const HISTORY_RANGE_LAST_24 = "last24";
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

/**
 * Last 24 months ending at the latest stored actual month (not today's date).
 * Example: latest Sep 2026 → Oct 2024 – Sep 2026.
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
    mode: HISTORY_RANGE_LAST_24,
  };
}

/**
 * Full available history for the selected category/code.
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
    mode: HISTORY_RANGE_ALL,
  };
}

/**
 * Resolve a historical display window.
 * Default mode is Last 24 months anchored on the latest stored actual month.
 */
export function resolveHistoricalDisplayWindow(
  latestIsoMonth,
  historyStartIso,
  rangeMode = HISTORY_RANGE_LAST_24
) {
  if (rangeMode === HISTORY_RANGE_ALL) {
    return resolveAllHistoryWindow(latestIsoMonth, historyStartIso);
  }
  return resolveLast24MonthsWindow(latestIsoMonth, historyStartIso);
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

/**
 * Build Historical Budget Analysis metrics for the selected display range.
 * Baseline months before the window are used only for YoY matching-month growth.
 */
export function buildHistoricalDisplayAnalysis(history, { rangeMode = HISTORY_RANGE_LAST_24 } = {}) {
  if (!history) {
    return null;
  }
  const latestIso =
    history.latest_actual_month
    || history.history_end
    || (Array.isArray(history.monthly_actuals) && history.monthly_actuals.length
      ? history.monthly_actuals[history.monthly_actuals.length - 1].month
      : null);
  const window = resolveHistoricalDisplayWindow(latestIso, history.history_start, rangeMode);
  if (!window) {
    return null;
  }

  const allMonths = Array.isArray(history.monthly_actuals) ? history.monthly_actuals : [];
  const displayMonths = filterMonthlyActualsToWindow(allMonths, window);
  const displayComplete = completeTotalsFromMonthly(displayMonths);
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
    rangeMode: window.mode || rangeMode,
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
    // Budget-code recurring totals require per-code months; the analysis API
    // already windows these with the same display rule for the default range.
    budget_codes: Array.isArray(history.budget_codes) ? history.budget_codes : [],
    recurring_count: history.recurring_count,
    budget_code_count: history.budget_code_count,
  };
}
