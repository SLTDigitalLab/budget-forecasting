import { addMonths, earliestForecastIso, inclusiveMonthCount, isoToLabel } from "./months.js";

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

export const FUTURE_YEAR_COUNT = 10;
export const MAX_FORECAST_MONTHS = 60;
export const MAX_FORECAST_END_ISO = "2030-12";

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

export function yearOptions(earliestIso, futureYears = FUTURE_YEAR_COUNT) {
  const firstYear = Number(String(earliestIso || "").slice(0, 4));
  if (!Number.isFinite(firstYear) || firstYear < 1) {
    return [];
  }
  const lastYear = Number(MAX_FORECAST_END_ISO.slice(0, 4));
  const years = [];
  const limit = Math.min(firstYear + Number(futureYears), lastYear);
  for (let year = firstYear; year <= limit; year += 1) {
    years.push(year);
  }
  return years;
}

export function lastAllowedIso(earliestIso, maxHorizon = MAX_FORECAST_MONTHS) {
  if (!earliestIso || !/^\d{4}-\d{2}$/.test(earliestIso)) {
    return MAX_FORECAST_END_ISO;
  }
  const byHorizon = addMonths(earliestIso, Number(maxHorizon) - 1);
  if (!byHorizon || byHorizon > MAX_FORECAST_END_ISO) {
    return MAX_FORECAST_END_ISO;
  }
  return byHorizon;
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

export function validateForecastPeriod({ startIso, endIso, earliestIso, historyEnd }) {
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
  if (startIso > MAX_FORECAST_END_ISO || endIso > MAX_FORECAST_END_ISO) {
    return "Forecast dates after December 2030 are not supported.";
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
    return "Forecast every remaining month of the selected year, through December.";
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

export function compactPeriodHelp({ startIso, endIso, earliestIso }) {
  const forecast = formatCompactPeriodRange(startIso, endIso);
  const available = earliestIso && /^\d{4}-\d{2}$/.test(earliestIso)
    ? `Available from ${monthYearPlain(earliestIso)}`
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

export function remainingYearOptions(earliestIso, futureYears = FUTURE_YEAR_COUNT, maxHorizon = MAX_FORECAST_MONTHS) {
  return yearOptions(earliestIso, futureYears).filter((year) => {
    const remaining = remainingMonthsOfYear(year, earliestIso);
    if (!remaining) {
      return false;
    }
    return inclusiveMonthCount(earliestIso, remaining.endIso) <= Number(maxHorizon);
  });
}

export function firstCompleteFinancialYear(earliestIso) {
  const { year, month } = splitIsoMonth(earliestIso);
  if (!year) {
    return null;
  }
  return Number(month) <= 1 ? Number(year) : Number(year) + 1;
}

export function lastCompleteFinancialYear(earliestIso, maxHorizon = MAX_FORECAST_MONTHS) {
  const lastIso = lastAllowedIso(earliestIso, maxHorizon);
  const { year, month } = splitIsoMonth(lastIso);
  if (!year) {
    return firstCompleteFinancialYear(earliestIso);
  }
  return Number(month) === 12 ? Number(year) : Number(year) - 1;
}

export function financialYearOptions(earliestIso, maxHorizon = MAX_FORECAST_MONTHS) {
  const first = firstCompleteFinancialYear(earliestIso);
  const last = lastCompleteFinancialYear(earliestIso, maxHorizon);
  if (!first || !last || last < first) {
    return first ? [first] : [];
  }
  const years = [];
  for (let year = first; year <= last; year += 1) {
    years.push(year);
  }
  return years;
}

export function financialYearRange(startYear, endYear, earliestIso) {
  const years = financialYearOptions(earliestIso);
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
  currentStartIso,
  currentEndIso,
}) {
  const startParts = splitIsoMonth(currentStartIso || earliestIso);
  const endParts = splitIsoMonth(currentEndIso || currentStartIso || earliestIso);
  if (periodType === PERIOD_TYPE.SPECIFIC_MONTH) {
    const iso = clampIsoToMinimum(
      year || startParts.year,
      month || startParts.month || "01",
      earliestIso,
      lastAllowedIso(earliestIso)
    );
    return { startIso: iso, endIso: iso };
  }
  if (periodType === PERIOD_TYPE.REMAINING_YEAR) {
    const options = remainingYearOptions(earliestIso);
    let selectedYear = Number(year || startParts.year);
    if (!options.includes(selectedYear)) {
      selectedYear = options[0];
    }
    const remaining = remainingMonthsOfYear(selectedYear, earliestIso);
    if (remaining) {
      return remaining;
    }
    return { startIso: earliestIso, endIso: defaultEndIso(earliestIso) };
  }
  if (periodType === PERIOD_TYPE.FINANCIAL_YEARS) {
    return (
      financialYearRange(startYear || startParts.year, endYear || endParts.year, earliestIso)
      || { startIso: earliestIso, endIso: defaultEndIso(earliestIso) }
    );
  }
  return { startIso: earliestIso, endIso: defaultEndIso(earliestIso) };
}

export function validatePeriodTypeSelection({
  periodType,
  startIso,
  endIso,
  earliestIso,
  historyEnd,
}) {
  const periodMessage = validateForecastPeriod({
    startIso,
    endIso,
    earliestIso,
    historyEnd,
  });
  if (periodMessage) {
    return periodMessage;
  }
  if (!periodType) {
    return "Forecast period type is required.";
  }
  if (periodType === PERIOD_TYPE.SPECIFIC_MONTH && startIso !== endIso) {
    return "A specific future month must have the same start and end month.";
  }
  if (periodType === PERIOD_TYPE.REMAINING_YEAR) {
    const expected = remainingMonthsOfYear(startIso.slice(0, 4), earliestIso);
    if (!expected || expected.startIso !== startIso || expected.endIso !== endIso) {
      return "Remaining months must run from the first valid month through December of the selected year.";
    }
  }
  if (periodType === PERIOD_TYPE.FINANCIAL_YEARS) {
    const expected = financialYearRange(startIso.slice(0, 4), endIso.slice(0, 4), earliestIso);
    if (!expected || expected.startIso !== startIso || expected.endIso !== endIso) {
      return "Future financial years must be complete January–December years after the current partial year.";
    }
  }
  return "";
}
