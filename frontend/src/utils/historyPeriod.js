export function formatHistoryPeriod(earliest, latest) {
  if (!earliest && !latest) {
    return "—";
  }
  if (earliest && latest && earliest !== latest) {
    return `${earliest} – ${latest}`;
  }
  return earliest || latest;
}
