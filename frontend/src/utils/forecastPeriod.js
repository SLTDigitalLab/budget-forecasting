import { earliestForecastIso, inclusiveMonthCount, isoToLabel } from "./months.js";

export const MONTH_OPTIONS = [
  { number: 1, value: "01", label: "January" },
  { number: 2, value: "02", label: "February" },
  { number: 3, value: "03", label: "March" },
  { number: 4, value: "04", label: "April" },
  { number: 5, value: "05", label: "May" },
  { number: 6, value: "06", label: "June" },
  { number: 7, value: "07", label: "July" },
  { number: 8, value: "08", label: "August" },
  { number: 9, value: "09", label: "September" },
  { number: 10, value: "10", label: "October" },
  { number: 11, value: "11", label: "November" },
  { number: 12, value: "12", label: "December" },
];

export const MAX_FORECAST_MONTHS = 60;

export const PERIOD_TYPE = {
  SPECIFIC_MONTH: "specific_month",
  REMAINING_YEAR: "remaining_year",
  FINANCIAL_YEARS: "financial_years",
};

export const PERIOD_TYPE_OPTIONS = [
  { value: PERIOD_TYPE.SPECIFIC_MONTH, label: "Specific future month" },
  { value: PERIOD_TYPE.REMAINING_YEAR, label: "Remaining months of the year" },
  { value: PERIOD_TYPE.FINANCIAL_YEARS, label: "Future financial years" },
];

export function padMonth(month) {
  return String(Number(month)).padStart(2, "0");
}

export function toIsoMonth(year, month) {
  return `${Number(year)}-${padMonth(month)}`;
}

export function splitIsoMonth(isoMonth) {
  if (!isoMonth || !/^\d{4}-\d{2}$/.test(isoMonth)) {
    return { year: "", month: "" };
  }
  const [year, month] = isoMonth.split("-");
  return { year, month };
}

export function yearOptions(earliestIso, maximumIso) {
  const firstYear = Number(String(earliestIso || "").slice(0, 4));
  const lastYear = Number(String(maximumIso || "").slice(0, 4));
  if (!Number.isFinite(firstYear) || firstYear < 1 || !Number.isFinite(lastYear) || lastYear < firstYear) {
    return [];
  }
  const years = [];
  for (let year = firstYear; year <= lastYear; year += 1) {
    years.push(year);
  }
  return years;
}

export function lastAllowedIso(maximumIso) {
  return /^\d{4}-\d{2}$/.test(maximumIso || "") ? maximumIso : "";
}

export function isMonthDisabled(year, month, minimumIso, maximumIso) {
  const iso = toIsoMonth(year, month);
  if (minimumIso && iso < minimumIso) {
    return true;
  }
  if (maximumIso && iso > maximumIso) {
    return true;
  }
  return false;
}

export function clampIsoToMinimum(year, month, minimumIso, maximumIso) {
  let iso = toIsoMonth(year, month);
  if (minimumIso && iso < minimumIso) {
    iso = minimumIso;
  }
  if (maximumIso && iso > maximumIso) {
    iso = maximumIso;
  }
  return iso;
}

export function defaultEndIso(earliestIso) {
  const { year } = splitIsoMonth(earliestIso);
  if (!year) {
    return earliestIso;
  }
  return toIsoMonth(year, 12);
}

export function firstValidForecastIso(historyEnd) {
  return earliestForecastIso(historyEnd);
}

export function buildForecastRequest(startYear, startMonth, endYear, endMonth) {
  return {
    start_month: toIsoMonth(startYear, startMonth),
    end_month: toIsoMonth(endYear, endMonth),
  };
}

export function validateForecastPeriod({ startIso, endIso, earliestIso, historyEnd, maximumIso }) {
  const { year: startYear, month: startMonth } = splitIsoMonth(startIso);
  const { year: endYear, month: endMonth } = splitIsoMonth(endIso);
  if (!startYear || !startMonth || !endYear || !endMonth) {
    return "Start year, start month, end year and end month are required.";
  }
  if (earliestIso && startIso < earliestIso) {
    const historyLabel = historyEnd || earliestIso;
    return `Start period must be after the model history end (${historyLabel}).`;
  }
  if (endIso < startIso) {
    return "End period cannot be before start period.";
  }
  const maximum = lastAllowedIso(maximumIso);
  if (!maximum) {
    return "The published forecast period is not available.";
  }
  if (startIso > maximum || endIso > maximum || (earliestIso && endIso < earliestIso)) {
    return `Forecast dates must be from ${monthYearPlain(earliestIso)} through ${monthYearPlain(maximum)}.`;
  }
  return "";
}

export function alignEndWithStart(startIso, endIso) {
  if (startIso && endIso && endIso < startIso) {
    return startIso;
  }
  return endIso;
}

export function periodTypeHelp(periodType) {
  if (periodType === PERIOD_TYPE.SPECIFIC_MONTH) {
    return "Forecast one selected month after the model history end.";
  }
  if (periodType === PERIOD_TYPE.REMAINING_YEAR) {
    return "Forecast every month after the latest stored actual through December of that same year.";
  }
  if (periodType === PERIOD_TYPE.FINANCIAL_YEARS) {
    return "Forecast complete January–December financial years.";
  }
  return "";
}

export function formatSelectedPeriod(startIso, endIso) {
  if (!startIso || !endIso) {
    return "—";
  }
  const startLabel = isoToLabel(startIso).replace("-", " ");
  if (startIso === endIso) {
    return startLabel;
  }
  return `${startLabel} to ${isoToLabel(endIso).replace("-", " ")}`;
}

export function monthYearPlain(isoMonth) {
  return isoToLabel(isoMonth || "").replace("-", " ");
}

export function formatCompactPeriodRange(startIso, endIso) {
  if (!startIso || !endIso) {
    return "";
  }
  const start = splitIsoMonth(startIso);
  const end = splitIsoMonth(endIso);
  const startMonth = isoToLabel(startIso).split("-")[0];
  const endMonth = isoToLabel(endIso).split("-")[0];
  if (startIso === endIso) {
    return `${startMonth} ${start.year}`;
  }
  if (start.year === end.year) {
    return `${startMonth}–${endMonth} ${end.year}`;
  }
  return `${startMonth} ${start.year}–${endMonth} ${end.year}`;
}

export function formatSelectedPeriodDisplay(startIso, endIso) {
  if (!startIso || !endIso) {
    return "—";
  }
  if (startIso === endIso) {
    return monthYearPlain(startIso);
  }
  return `${monthYearPlain(startIso)} – ${monthYearPlain(endIso)}`;
}

export function compactPeriodHelp({ startIso, endIso, earliestIso, maximumIso }) {
  const forecast = formatCompactPeriodRange(startIso, endIso);
  const available = earliestIso && lastAllowedIso(maximumIso)
    ? `Available forecast period: ${formatCompactPeriodRange(earliestIso, maximumIso)}`
    : "";
  if (forecast && available) {
    return `Forecasts ${forecast} · ${available}`;
  }
  if (forecast) {
    return `Forecasts ${forecast}`;
  }
  return available;
}

export function remainingMonthsOfYear(year, earliestIso) {
  const yearNumber = Number(year);
  if (!Number.isFinite(yearNumber) || yearNumber < 1) {
    return null;
  }
  const yearStart = toIsoMonth(yearNumber, 1);
  const yearEnd = toIsoMonth(yearNumber, 12);
  if (earliestIso && yearEnd < earliestIso) {
    return null;
  }
  const startIso = earliestIso && earliestIso > yearStart ? earliestIso : yearStart;
  if (String(startIso).slice(0, 4) !== String(yearNumber)) {
    return null;
  }
  return { startIso, endIso: yearEnd };
}

/**
 * Remaining months of the year: month after the latest stored actual through
 * December of that same year. Does not invent a period when metadata is missing.
 */
export function remainingMonthsFromLatestActual(latestActualIso, { earliestIso = "", maximumIso = "" } = {}) {
  if (!/^\d{4}-\d{2}$/.test(latestActualIso || "")) {
    return {
      available: false,
      reason: "missing",
      startIso: "",
      endIso: "",
      latestActualIso: "",
      message: "Remaining months cannot be derived until the latest stored actual month is available.",
    };
  }
  const { year, month } = splitIsoMonth(latestActualIso);
  const monthNumber = Number(month);
  const yearNumber = Number(year);
  if (monthNumber === 12) {
    return {
      available: false,
      reason: "december",
      startIso: "",
      endIso: "",
      latestActualIso,
      message: `There are no remaining months after ${monthYearPlain(latestActualIso)}. Use Future financial years instead.`,
    };
  }
  const startIso = toIsoMonth(yearNumber, monthNumber + 1);
  const endIso = toIsoMonth(yearNumber, 12);
  const span = inclusiveMonthCount(startIso, endIso);
  if (span < 1 || span > MAX_FORECAST_MONTHS) {
    return {
      available: false,
      reason: "span",
      startIso,
      endIso,
      latestActualIso,
      message: "The remaining-months period is outside the supported forecast horizon.",
    };
  }
  const maximum = lastAllowedIso(maximumIso);
  if (earliestIso && (startIso < earliestIso || endIso < earliestIso)) {
    return {
      available: false,
      reason: "model_horizon",
      startIso,
      endIso,
      latestActualIso,
      message: maximum
        ? `Remaining months ${formatCompactPeriodRange(startIso, endIso)} are not available from the published model. Choose a period from ${monthYearPlain(earliestIso)} through ${monthYearPlain(maximum)}.`
        : `Remaining months ${formatCompactPeriodRange(startIso, endIso)} are not available from the published model.`,
    };
  }
  if (maximum && (startIso > maximum || endIso > maximum)) {
    return {
      available: false,
      reason: "model_horizon",
      startIso,
      endIso,
      latestActualIso,
      message: earliestIso
        ? `Remaining months ${formatCompactPeriodRange(startIso, endIso)} are not available from the published model. Choose a period from ${monthYearPlain(earliestIso)} through ${monthYearPlain(maximum)}.`
        : `Remaining months ${formatCompactPeriodRange(startIso, endIso)} are not available from the published model.`,
    };
  }
  return {
    available: true,
    reason: "",
    startIso,
    endIso,
    latestActualIso,
    message: "",
  };
}

export function remainingYearOptions(earliestIso, maximumIso) {
  return yearOptions(earliestIso, maximumIso).filter((year) => {
    const remaining = remainingMonthsOfYear(year, earliestIso);
    if (!remaining || (maximumIso && remaining.endIso > maximumIso)) {
      return false;
    }
    const span = inclusiveMonthCount(remaining.startIso, remaining.endIso);
    return span >= 1 && span <= MAX_FORECAST_MONTHS;
  });
}

export function firstCompleteFinancialYear(earliestIso) {
  const { year, month } = splitIsoMonth(earliestIso);
  if (!year) {
    return null;
  }
  return Number(month) <= 1 ? Number(year) : Number(year) + 1;
}

export function lastCompleteFinancialYear(maximumIso) {
  const lastIso = lastAllowedIso(maximumIso);
  const { year, month } = splitIsoMonth(lastIso);
  if (!year || Number(month) !== 12) {
    return null;
  }
  return Number(year);
}

export function financialYearOptions(earliestIso, maximumIso) {
  const first = firstCompleteFinancialYear(earliestIso);
  const last = lastCompleteFinancialYear(maximumIso);
  if (!first || !last || last < first) {
    return [];
  }
  const years = [];
  for (let year = first; year <= last; year += 1) {
    years.push(year);
  }
  return years;
}

export function financialYearRange(startYear, endYear, earliestIso, maximumIso) {
  const years = financialYearOptions(earliestIso, maximumIso);
  const first = years[0];
  const last = years[years.length - 1];
  if (!first) {
    return null;
  }
  let start = Number(startYear);
  let end = Number(endYear);
  if (!Number.isFinite(start) || start < first) {
    start = first;
  }
  if (last && start > last) {
    start = last;
  }
  if (!Number.isFinite(end) || end < start) {
    end = start;
  }
  if (last && end > last) {
    end = last;
  }
  return {
    startIso: toIsoMonth(start, 1),
    endIso: toIsoMonth(end, 12),
  };
}

export function resolveForecastPeriod({
  periodType,
  year,
  month,
  startYear,
  endYear,
  earliestIso,
  maximumIso,
  latestActualIso,
  currentStartIso,
  currentEndIso,
}) {
  const startParts = splitIsoMonth(currentStartIso || earliestIso);
  const endParts = splitIsoMonth(currentEndIso || currentStartIso || earliestIso);
  const maximum = lastAllowedIso(maximumIso);
  if (periodType === PERIOD_TYPE.SPECIFIC_MONTH) {
    const iso = clampIsoToMinimum(
      year || startParts.year,
      month || startParts.month || "01",
      earliestIso,
      maximum
    );
    return { startIso: iso, endIso: iso };
  }
  if (periodType === PERIOD_TYPE.REMAINING_YEAR) {
    const remaining = remainingMonthsFromLatestActual(latestActualIso, {
      earliestIso,
      maximumIso: maximum,
    });
    if (remaining.available) {
      return { startIso: remaining.startIso, endIso: remaining.endIso };
    }
    return { startIso: "", endIso: "" };
  }
  if (periodType === PERIOD_TYPE.FINANCIAL_YEARS) {
    return (
      financialYearRange(startYear || startParts.year, endYear || endParts.year, earliestIso, maximum)
      || { startIso: "", endIso: "" }
    );
  }
  return { startIso: "", endIso: "" };
}

export function validatePeriodTypeSelection({
  periodType,
  startIso,
  endIso,
  earliestIso,
  historyEnd,
  maximumIso,
  latestActualIso,
}) {
  if (!periodType) {
    return "Forecast period type is required.";
  }
  if (periodType === PERIOD_TYPE.REMAINING_YEAR) {
    const remaining = remainingMonthsFromLatestActual(latestActualIso, {
      earliestIso,
      maximumIso,
    });
    if (!remaining.available) {
      return remaining.message;
    }
    if (remaining.startIso !== startIso || remaining.endIso !== endIso) {
      return `Remaining months must be ${formatSelectedPeriodDisplay(remaining.startIso, remaining.endIso)} based on the latest stored actual (${monthYearPlain(latestActualIso)}).`;
    }
  }
  const periodMessage = validateForecastPeriod({
    startIso,
    endIso,
    earliestIso,
    historyEnd,
    maximumIso,
  });
  if (periodMessage) {
    return periodMessage;
  }
  if (periodType === PERIOD_TYPE.SPECIFIC_MONTH && startIso !== endIso) {
    return "A specific future month must have the same start and end month.";
  }
  if (periodType === PERIOD_TYPE.FINANCIAL_YEARS) {
    const expected = financialYearRange(startIso.slice(0, 4), endIso.slice(0, 4), earliestIso, maximumIso);
    if (!expected || expected.startIso !== startIso || expected.endIso !== endIso) {
      return "Future financial years must be complete January–December years after the current partial year.";
    }
  }
  return "";
}
