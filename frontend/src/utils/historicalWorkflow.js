export function confirmationBlocked(preview) {
  return !preview || preview.can_confirm !== true;
}

export function duplicateRecordCount(preview) {
  return (preview?.duplicate_conflicts || []).reduce((total, item) => total + (item.records || []).length, 0);
}

export function summaryValue(preview, key) {
  return preview?.summary?.[key];
}

export function cellClass(highlight) {
  if (highlight === "new") return "settings-cell-new";
  if (highlight === "updated") return "settings-cell-updated";
  return undefined;
}

export function displayAmount(value) {
  if (value === null || value === undefined || value === "") return "-";
  return value;
}
