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

export function addMonths(isoMonth, count) {
  const [yearText, monthText] = isoMonth.split("-");
  const index = Number(yearText) * 12 + (Number(monthText) - 1) + count;
  const year = Math.floor(index / 12);
  const month = (index % 12) + 1;
  return `${year}-${String(month).padStart(2, "0")}`;
}

export function labelToIso(label) {
  if (!label || !label.includes("-")) {
    return null;
  }
  const [monthName, yearText] = label.split("-");
  const month = MONTH_LABELS.indexOf(monthName) + 1;
  if (month < 1) {
    return null;
  }
  return `${yearText}-${String(month).padStart(2, "0")}`;
}

export function isoToLabel(isoMonth) {
  const [yearText, monthText] = isoMonth.split("-");
  const month = Number(monthText);
  if (!month || month < 1 || month > 12) {
    return isoMonth;
  }
  return `${MONTH_LABELS[month - 1]}-${yearText}`;
}

export function inclusiveMonthCount(startIso, endIso) {
  const [startYear, startMonth] = startIso.split("-").map(Number);
  const [endYear, endMonth] = endIso.split("-").map(Number);
  return (endYear - startYear) * 12 + (endMonth - startMonth) + 1;
}

export function earliestForecastIso(historyEnd) {
  if (!historyEnd) {
    return "2026-07";
  }
  if (/^\d{4}-\d{2}$/.test(historyEnd)) {
    return addMonths(historyEnd, 1);
  }
  const trainingIso = labelToIso(historyEnd);
  return addMonths(trainingIso || "2026-06", 1);
}
