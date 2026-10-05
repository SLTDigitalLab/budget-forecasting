import { isoToLabel } from "./months.js";

function normalizedUnit(amountUnit) {
  return String(amountUnit || "LKR")
    .trim()
    .replace(/[\s-]+/g, "_")
    .toUpperCase();
}

export function isPresentAmount(value) {
  if (value == null || value === "") {
    return false;
  }
  return Number.isFinite(Number(value));
}

export function formatBoundAmount(value, amountUnit = "LKR") {
  if (!isPresentAmount(value)) {
    return "Unavailable";
  }
  return formatAmount(Number(value), amountUnit);
}

export function formatAmount(value, amountUnit = "LKR") {
  const amount = Number(value);
  const unit = (amountUnit || "LKR").trim() || "LKR";
  if (!Number.isFinite(amount)) {
    return "LKR —";
  }
  const formatted = amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const normalized = normalizedUnit(unit);
  if (
    normalized === "LKR_MILLIONS" ||
    normalized === "LKR_MILLION" ||
    normalized.includes("MILLION")
  ) {
    return `LKR ${formatted} Mn`;
  }
  return `${unit} ${formatted}`;
}

export function formatPercentChange(percent) {
  if (!Number.isFinite(percent)) {
    return "—";
  }
  if (Math.abs(percent) < 0.01) {
    return "0.00%";
  }
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent.toFixed(2)}%`;
}

export function chronologicalForecasts(forecast) {
  const rows = Array.isArray(forecast?.monthly_forecasts)
    ? forecast.monthly_forecasts.slice()
    : [];
  return rows
    .filter((row) => row && row.month)
    .sort((left, right) => String(left.month).localeCompare(String(right.month)));
}

export function axisUnitLabel(amountUnit = "LKR") {
  const normalized = normalizedUnit(amountUnit);
  if (
    normalized === "LKR_MILLIONS" ||
    normalized === "LKR_MILLION" ||
    normalized.includes("MILLION")
  ) {
    return "LKR Mn";
  }
  return (amountUnit || "LKR").trim() || "LKR";
}

export function formatAxisTick(value, amountUnit = "LKR") {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "";
  }
  return amount.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

export function periodLabel(forecast) {
  if (!forecast?.requested_start_month || !forecast?.requested_end_month) {
    return "—";
  }
  return `${isoToLabel(forecast.requested_start_month)} to ${isoToLabel(forecast.requested_end_month)}`;
}
