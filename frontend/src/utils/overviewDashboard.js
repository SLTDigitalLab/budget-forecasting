import { isAllCategories, isInternationalSettlementCategory } from "./forecastCategory.js";

const CALENDAR_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

export const STORED_FORECAST_STATUS = "Completed";

function finiteAmount(value) {
  if (value == null || value === "") {
    return null;
  }
  const amount = Number(value);
  return Number.isFinite(amount) ? amount : null;
}

function monthKey(value) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || "").trim());
  if (!match) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12) {
    return null;
  }
  return { year, month, iso: `${match[1]}-${match[2]}` };
}

/**
 * Sum finite amounts that share a YYYY-MM key.
 * A blank, null, or non-finite amount does not create a month and is not treated as zero.
 */
export function aggregateMonthlyAmounts(rows, amountKey) {
  const totals = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const key = monthKey(row?.month);
    const amount = finiteAmount(row?.[amountKey]);
    if (!key || amount == null) {
      continue;
    }
    totals.set(key.iso, (totals.get(key.iso) || 0) + amount);
  }
  return totals;
}

/**
 * Client-side helper for aggregated monthly totals only.
 * Live Overview/Settings/Analytics use API `latest_complete_year` from
 * `coverage_report`, which requires every applicable Budget Code (including Book Entry)
 * to have a numeric actual or explicit missing declaration for all Jan–Dec months.
 * This helper only checks that each calendar month has a finite combined total
 * (zero counts; null does not). It must not invent a complete year from one month.
 * Missing months are not filled. Forecast rows must not be passed in.
 */
export function latestCompleteActualYear(rows) {
  const totals = aggregateMonthlyAmounts(rows, "actual_amount");
  const covered = new Map();
  for (const iso of totals.keys()) {
    const key = monthKey(iso);
    if (!key) {
      continue;
    }
    if (!covered.has(key.year)) {
      covered.set(key.year, new Set());
    }
    covered.get(key.year).add(key.month);
  }
  const complete = [...covered.entries()]
    .filter(([, months]) => CALENDAR_MONTHS.every((month) => months.has(month)))
    .map(([year]) => year);
  if (!complete.length) {
    return null;
  }
  return Math.max(...complete);
}

export const DECLARED_MISSING_TOTAL_NOTE = "Declared missing values are submitted but are not included in totals.";

export function latestCompleteYearLabel(year) {
  if (year == null || year === "") {
    return "Latest complete year: unavailable";
  }
  return `Latest complete year: ${year}`;
}

export function overviewLatestYearLabel(year) {
  if (year == null || year === "") {
    return "Latest Year: unavailable";
  }
  return `Latest Year: ${year}`;
}

export function overviewCompleteYearUnavailableMessage(historical) {
  const year = historical?.latest_complete_year;
  if (year != null && year !== "") {
    return "";
  }
  if (historical?.historical_observations_exist) {
    return "No complete year available. Historical actuals exist, but no calendar year has a numeric actual or explicit missing declaration for every applicable Budget Code in all twelve months.";
  }
  return "No complete year available.";
}

export function latestAvailableDataValue(source) {
  const month = source?.latest_available_month;
  if (!month) {
    return "Unavailable";
  }
  const status = source.latest_available_status === "complete" ? "Complete" : "Partial";
  const submitted = Number(source.latest_available_submitted) || 0;
  const applicable = Number(source.latest_available_applicable) || 0;
  const declared = Number(source.latest_available_declared_missing) || 0;
  const declaredNote = declared > 0 ? `, ${declared} declared missing` : "";
  return `${month} — ${status}, ${submitted}/${applicable} submitted${declaredNote}`;
}

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function categoryText(category) {
  if (category == null) {
    return "";
  }
  if (typeof category === "object") {
    return String(category.name || category.source_name || category.id || "").trim();
  }
  return String(category).trim();
}

export function categoriesReferToSame(left, right) {
  if (isInternationalSettlementCategory(left) && isInternationalSettlementCategory(right)) {
    return true;
  }
  const leftNames = [categoryText(left), left?.id].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
  const rightNames = [categoryText(right), right?.id].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
  return leftNames.some((name) => rightNames.includes(name));
}

function forecastRecordApplies(record, category) {
  if (!record) {
    return false;
  }
  if (isAllCategories(record.category)) {
    return true;
  }
  return categoriesReferToSame(record.category, category);
}

/** Records must already be newest first. An All Categories run is applicable. */
export function applicableForecastCandidates(records, category) {
  const list = Array.isArray(records) ? records : [];
  return list.filter((record) => forecastRecordApplies(record, category));
}

/** Records must already be newest first. An All Categories run is applicable. */
export function selectLatestApplicableForecast(records, category) {
  return applicableForecastCandidates(records, category)[0] || null;
}

function generatedTimestamp(record) {
  const time = Date.parse(record?.generated_at || "");
  return Number.isFinite(time) ? time : null;
}

/** Newest successfully saved run by generated timestamp. Category is not a filter. */
export function sortForecastHistoryNewestFirst(records) {
  return (Array.isArray(records) ? records : []).filter(Boolean).sort((left, right) => {
    const leftTime = generatedTimestamp(left);
    const rightTime = generatedTimestamp(right);
    if (leftTime == null && rightTime == null) {
      return (Number(right.id) || 0) - (Number(left.id) || 0);
    }
    if (leftTime == null) {
      return 1;
    }
    if (rightTime == null) {
      return -1;
    }
    if (rightTime !== leftTime) {
      return rightTime - leftTime;
    }
    return (Number(right.id) || 0) - (Number(left.id) || 0);
  });
}

export function selectNewestForecast(records) {
  return sortForecastHistoryNewestFirst(records)[0] || null;
}

function monthsFromForecastEntries(entries) {
  const monthTotals = new Map();
  const codes = [];
  entries.forEach((entry) => {
    const amounts = entry?.forecast;
    if (!amounts || typeof amounts !== "object") {
      return;
    }
    let codeTotal = 0;
    let any = false;
    Object.entries(amounts).forEach(([month, value]) => {
      const amount = finiteAmount(value);
      const key = monthKey(month);
      if (amount == null || !key) {
        return;
      }
      monthTotals.set(key.iso, (monthTotals.get(key.iso) || 0) + amount);
      codeTotal += amount;
      any = true;
    });
    if (!any) {
      return;
    }
    codes.push({
      budget_code: String(entry.budget_code || "").trim(),
      account_name: String(entry.account_name || ""),
      forecast_amount: codeTotal,
    });
  });
  return { monthTotals, codes };
}

function monthPointsFromTotals(monthTotals) {
  return [...monthTotals.keys()].sort().map((iso) => {
    const key = monthKey(iso);
    return {
      month: iso,
      label: key ? MONTH_LABELS[key.month - 1] : iso,
      year: key?.year ?? null,
      forecast_amount: monthTotals.get(iso),
    };
  });
}

function monthPointsFromSavedSeries(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => {
      const key = monthKey(row?.month);
      const amount = finiteAmount(row?.forecast_amount);
      if (!key || amount == null) {
        return null;
      }
      return {
        month: key.iso,
        label: MONTH_LABELS[key.month - 1],
        year: key.year,
        forecast_amount: amount,
      };
    })
    .filter(Boolean)
    .sort((left, right) => left.month.localeCompare(right.month));
}

/**
 * The whole saved run. Months are only those stored on the run.
 * Missing months are omitted and are not replaced with zero.
 */
export function forecastViewFromRun(run) {
  const entries = (Array.isArray(run?.budget_code_forecasts) ? run.budget_code_forecasts : [])
    .filter((entry) => entry?.available !== false);
  const { monthTotals, codes } = monthsFromForecastEntries(entries);
  const savedMonths = monthPointsFromSavedSeries(run?.monthly_forecasts);
  const months = savedMonths.length ? savedMonths : monthPointsFromTotals(monthTotals);
  if (!months.length || !codes.some((row) => row.budget_code)) {
    return null;
  }
  const total = codes.reduce((sum, row) => sum + row.forecast_amount, 0);
  const overall = finiteAmount(run?.overall_total);
  return {
    runId: run?.id ?? null,
    category: String(run?.category || "").trim(),
    total,
    average: months.length ? total / months.length : null,
    share: overall == null || overall === 0 ? null : total / overall,
    codeCount: codes.filter((row) => row.budget_code).length,
    months,
    codes: shareDistribution(codes, "forecast_amount"),
    forecastYear: new Set(months.map((row) => row.year)).size === 1 ? months[0].year : null,
  };
}

export function actualYearSeries(rows, year) {
  if (!Number.isFinite(Number(year))) {
    return [];
  }
  const totals = aggregateMonthlyAmounts(rows, "actual_amount");
  return MONTH_LABELS.map((label, index) => {
    const iso = `${year}-${String(index + 1).padStart(2, "0")}`;
    return {
      label,
      month: iso,
      year: Number(year),
      actual_amount: totals.has(iso) ? totals.get(iso) : null,
    };
  });
}

export function sliceTooltipDetails(entry) {
  const source = entry?.payload?.budget_code ? entry.payload : entry;
  const budgetCode = String(source?.budget_code || "").trim();
  if (!budgetCode) {
    return null;
  }
  return {
    budget_code: budgetCode,
    account_name: String(source.account_name || ""),
    amount: source.amount,
    percent: source.percent,
  };
}

export function shareDistribution(rows, amountKey) {
  const items = (Array.isArray(rows) ? rows : [])
    .map((row) => ({
      budget_code: String(row?.budget_code || "").trim(),
      account_name: String(row?.account_name || ""),
      amount: finiteAmount(row?.[amountKey] ?? row?.amount),
    }))
    .filter((row) => row.budget_code && row.amount != null);
  const total = items.reduce((sum, row) => sum + row.amount, 0);
  const magnitude = items.reduce((sum, row) => sum + Math.abs(row.amount), 0);
  return items
    .sort((left, right) => right.amount - left.amount || left.budget_code.localeCompare(right.budget_code))
    .map((row) => ({
      ...row,
      // Signed percent of absolute magnitude; keep signed amounts for bar charts.
      percent: magnitude === 0 ? 0 : row.amount / magnitude,
      total,
      magnitude,
    }));
}

/**
 * One saved run only. Monthly totals and Budget Code totals both come from that run's
 * category slice. Missing amounts stay absent. Category share uses this run's overall total.
 */
export function categoryForecastFromRun(run, category) {
  const entries = (Array.isArray(run?.budget_code_forecasts) ? run.budget_code_forecasts : [])
    .filter((entry) => entry?.available !== false && categoriesReferToSame(entry?.category, category));
  const monthTotals = new Map();
  const codes = [];
  entries.forEach((entry) => {
    const amounts = entry?.forecast;
    if (!amounts || typeof amounts !== "object") {
      return;
    }
    let codeTotal = 0;
    let any = false;
    Object.entries(amounts).forEach(([month, value]) => {
      const amount = finiteAmount(value);
      const key = monthKey(month);
      if (amount == null || !key) {
        return;
      }
      monthTotals.set(key.iso, (monthTotals.get(key.iso) || 0) + amount);
      codeTotal += amount;
      any = true;
    });
    if (!any) {
      return;
    }
    codes.push({
      budget_code: String(entry.budget_code || "").trim(),
      account_name: String(entry.account_name || ""),
      forecast_amount: codeTotal,
    });
  });
  const months = [...monthTotals.keys()].sort().map((iso) => {
    const key = monthKey(iso);
    return {
      month: iso,
      label: key ? MONTH_LABELS[key.month - 1] : iso,
      year: key?.year ?? null,
      forecast_amount: monthTotals.get(iso),
    };
  });
  if (!months.length || !codes.some((row) => row.budget_code)) {
    return null;
  }
  const total = codes.reduce((sum, row) => sum + row.forecast_amount, 0);
  const overall = finiteAmount(run?.overall_total);
  const average = months.length ? total / months.length : null;
  return {
    runId: run?.id ?? null,
    total,
    average,
    share: overall == null || overall === 0 ? null : total / overall,
    codeCount: codes.filter((row) => row.budget_code).length,
    months,
    codes: shareDistribution(codes, "forecast_amount"),
    forecastYear: new Set(months.map((row) => row.year)).size === 1 ? months[0].year : null,
  };
}
