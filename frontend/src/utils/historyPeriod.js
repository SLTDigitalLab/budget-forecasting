const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function shortMonthLabel(isoMonth) {
  const [yearText, monthText] = String(isoMonth || "").split("-");
  const month = Number(monthText);
  if (!month || month < 1 || month > 12 || !yearText) {
    return isoMonth || "";
  }
  return `${MONTH_LABELS[month - 1]} ${yearText}`;
}

export function formatHistoryPeriod(earliest, latest) {
  if (!earliest && !latest) {
    return "—";
  }
  const start = shortMonthLabel(earliest);
  const end = shortMonthLabel(latest);
  if (start && end && start !== end) {
    return `${start} – ${end}`;
  }
  return start || end;
}
