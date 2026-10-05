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
 * Dataset-level completeness.
 * Historical rows are already monthly totals (one amount per calendar month, summed across
 * Budget Codes or categories). A month counts only when that total is a finite number,
 * including zero. A year qualifies only when months 01 through 12 are all present.
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

export function shareDistribution(rows, amountKey) {
  const items = (Array.isArray(rows) ? rows : [])
    .map((row) => ({
      budget_code: String(row?.budget_code || "").trim(),
      account_name: String(row?.account_name || ""),
      amount: finiteAmount(row?.[amountKey] ?? row?.amount),
    }))
    .filter((row) => row.budget_code && row.amount != null);
  const total = items.reduce((sum, row) => sum + row.amount, 0);
  return items
    .sort((left, right) => right.amount - left.amount || left.budget_code.localeCompare(right.budget_code))
    .map((row) => ({
      ...row,
      percent: total === 0 ? 0 : row.amount / total,
      total,
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
