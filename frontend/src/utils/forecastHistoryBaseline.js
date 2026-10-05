import { formatAmount } from "./forecastDisplay.js";
import { isoToLabel } from "./months.js";

const MINUS = "\u2212";
export const SPECIFIC_MONTH_UNAVAILABLE = "Specific-month comparison unavailable";
const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

function isIsoMonth(value) {
  return /^\d{4}-\d{2}$/.test(String(value || ""));
}

function calendarMonth(isoMonth) {
  return String(isoMonth || "").slice(5, 7);
}

export function calendarMonthName(isoMonth) {
  const month = Number(calendarMonth(isoMonth));
  if (!month || month < 1 || month > 12) {
    return "";
  }
  return MONTH_NAMES[month - 1];
}

function finiteHistoricalAmounts(actuals, predicate) {
  const amounts = [];
  (Array.isArray(actuals) ? actuals : []).forEach((row) => {
    if (predicate && !predicate(row)) {
      return;
    }
    const raw = row?.actual_amount;
    if (raw == null || raw === "") {
      return;
    }
    const amount = Number(raw);
    if (Number.isFinite(amount)) {
      amounts.push(amount);
    }
  });
  return amounts;
}

export function historicalMonthlyBaseline(forecast) {
  const declaredAverage = Number(forecast?.historical_monthly_average);
  const declaredCount = Number(forecast?.historical_month_count);
  if (Number.isFinite(declaredAverage) && Number.isFinite(declaredCount) && declaredCount > 0) {
    return { average: declaredAverage, count: declaredCount };
  }
  const amounts = finiteHistoricalAmounts(forecast?.historical_actuals);
  if (!amounts.length) {
    return { average: null, count: 0 };
  }
  const total = amounts.reduce((sum, value) => sum + value, 0);
  return { average: total / amounts.length, count: amounts.length };
}

export function specificMonthHistoricalBaseline(forecast, isoMonth) {
  const match = (Array.isArray(forecast?.monthly_forecasts) ? forecast.monthly_forecasts : []).find(
    (row) => row?.month === isoMonth
  );
  const declaredAverage = Number(match?.specific_month_historical_average);
  const declaredCount = Number(match?.specific_month_history_count);
  if (Number.isFinite(declaredAverage) && Number.isFinite(declaredCount) && declaredCount > 0) {
    return { average: declaredAverage, count: declaredCount };
  }
  if (!isIsoMonth(isoMonth)) {
    return { average: null, count: 0 };
  }
  const target = calendarMonth(isoMonth);
  const amounts = finiteHistoricalAmounts(forecast?.historical_actuals, (row) => {
    const month = String(row?.month || "");
    return isIsoMonth(month) && calendarMonth(month) === target && month < isoMonth;
  });
  if (!amounts.length) {
    return { average: null, count: 0 };
  }
  const total = amounts.reduce((sum, value) => sum + value, 0);
  return { average: total / amounts.length, count: amounts.length };
}

export function changeVsHistory(forecastAmount, historicalAverage) {
  const forecast = Number(forecastAmount);
  const average = Number(historicalAverage);
  if (!Number.isFinite(forecast) || !Number.isFinite(average)) {
    return { amount: null, percent: null };
  }
  const amount = forecast - average;
  if (average === 0) {
    return { amount, percent: forecast === 0 ? 0 : null };
  }
  return { amount, percent: (amount / average) * 100 };
}

export function formatSignedAmount(value, amountUnit = "LKR") {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "—";
  }
  if (amount === 0) {
    return formatAmount(0, amountUnit);
  }
  const formatted = formatAmount(Math.abs(amount), amountUnit);
  const sign = amount > 0 ? "+" : MINUS;
  return formatted.replace(/^(\S+)\s+/, `$1 ${sign}`);
}

export function formatSignedPercent(percent) {
  if (!Number.isFinite(percent)) {
    return "—";
  }
  if (percent === 0 || Math.abs(percent) < 0.005) {
    return "0.00%";
  }
  if (percent > 0) {
    return `+${percent.toFixed(2)}%`;
  }
  return `${MINUS}${Math.abs(percent).toFixed(2)}%`;
}

export function formatChangeVsHistory(amount, percent, amountUnit = "LKR") {
  if (!Number.isFinite(amount)) {
    return "—";
  }
  const signedAmount = formatSignedAmount(amount, amountUnit);
  if (!Number.isFinite(percent)) {
    return signedAmount;
  }
  return `${signedAmount} (${formatSignedPercent(percent)})`;
}

export function formatStackedChange(amount, percent, amountUnit = "LKR") {
  if (!Number.isFinite(amount)) {
    return { amount: "—", percent: "—" };
  }
  return {
    amount: formatSignedAmount(amount, amountUnit),
    percent: Number.isFinite(percent) ? formatSignedPercent(percent) : "—",
  };
}

export function historicalAverageCaption(count) {
  if (!Number.isFinite(Number(count)) || Number(count) < 1) {
    return "Overall historical monthly average";
  }
  return `Overall historical monthly average (${Number(count)} months)`;
}

export function calendarMonthShortName(isoMonth) {
  const short = isoToLabel(isoMonth || "").split("-")[0];
  return /^[A-Za-z]{3}$/.test(short) ? short : "";
}

export function specificMonthAverageLabel(isoMonth) {
  const name = calendarMonthShortName(isoMonth);
  return name ? `Historical ${name} Average` : "Historical Same-Month Average";
}

export function specificMonthChangeLabel(isoMonth) {
  const name = calendarMonthShortName(isoMonth);
  return name ? `Vs ${name} Average` : "Vs Same Month Average";
}

export function tooltipChangeTone(amount, percent) {
  if (!Number.isFinite(amount) || !Number.isFinite(percent)) {
    return "unavailable";
  }
  if (amount === 0 || Math.abs(percent) < 0.005) {
    return "zero";
  }
  return amount > 0 ? "up" : "down";
}

export function formatTooltipChange(amount, percent, amountUnit = "LKR") {
  if (!Number.isFinite(amount) || !Number.isFinite(percent)) {
    return "Unavailable";
  }
  return formatChangeVsHistory(amount, percent, amountUnit);
}

export function pointInsight(month, amount, percent, amountUnit = "LKR") {
  const label = isoToLabel(month || "");
  const change = formatChangeVsHistory(amount, percent, amountUnit);
  if (!label || change === "—") {
    return "";
  }
  return `${label} is ${change} versus the overall historical monthly average.`;
}

export function specificMonthInsight(month, amount, percent, amountUnit = "LKR") {
  const label = isoToLabel(month || "");
  const name = calendarMonthName(month);
  if (!label || !name) {
    return "";
  }
  if (!Number.isFinite(amount)) {
    return `${label}: ${SPECIFIC_MONTH_UNAVAILABLE}.`;
  }
  return `${label} is ${formatChangeVsHistory(amount, percent, amountUnit)} versus the historical ${name} average.`;
}
