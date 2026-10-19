export function confirmationBlocked(preview) {
  return !preview || preview.can_confirm !== true;
}

export function confirmButtonVisible(preview) {
  return Boolean(preview) && preview.needs_sheet_selection !== true;
}

function decisionCovers(decisions, item, fields) {
  return (decisions || []).some((entry) => fields.every((field) => entry[field] === item[field]) && entry.action);
}

export function confirmationBlockers(preview) {
  return reviewConfirmationState(preview).reasons;
}

export function reviewConfirmationState(preview, decisions = {}) {
  if (!confirmButtonVisible(preview)) {
    return {
      visible: false,
      disabled: true,
      reasons: ["Select a sheet to preview the changes."],
    };
  }
  const missingDecisions = decisions.missingDecisions || preview.missing_decisions || [];
  const metadataDecisions = decisions.metadataDecisions || preview.metadata_decisions || [];
  const removed = new Set(decisions.removed || preview.removed_stage_ids || []);
  const reasons = [];
  if (decisions.dirty) {
    reasons.push("Revalidate staged edits with Save Changes before Confirm & Save.");
  }
  const unresolved = Number(preview.summary?.unresolved_blank_cells ?? preview.unresolved_blank_count ?? 0);
  if (unresolved > 0) {
    reasons.push("Enter a number, zero, a negative amount, or '-' for every unresolved blank cell.");
  }
  const openMissing = (preview.missing_conflicts || []).filter(
    (item) => !decisionCovers(missingDecisions, item, ["budget_code", "month"]),
  );
  if (openMissing.length) {
    reasons.push("Choose keep, clear, declare missing, or remove for every blank that overlaps a stored value.");
  }
  const openMetadata = (preview.metadata_conflicts || []).filter(
    (item) => !decisionCovers(metadataDecisions, item, ["budget_code"]),
  );
  if (openMetadata.length) {
    reasons.push("Choose keep master or accept upload for every metadata conflict.");
  }
  const openDuplicates = (preview.duplicate_conflicts || []).filter((conflict) =>
    (conflict.records || []).some((record) => !removed.has(record.stage_id)),
  );
  if (openDuplicates.length) {
    reasons.push("Resolve duplicate Budget Code and month rows.");
  }
  const invalid = Number(preview.summary?.invalid_values ?? preview.invalid_count ?? 0);
  if (invalid > 0) {
    reasons.push("Correct invalid amounts.");
  }
  if ((preview.cell_errors || []).length && unresolved === 0 && invalid === 0) {
    reasons.push("Resolve the cell validation issues listed above.");
  }
  if ((preview.structural_errors?.length || preview.errors?.length) && reasons.length === 0) {
    reasons.push("Resolve the validation issues listed above.");
  }
  const hasLocalResolution = missingDecisions.length > 0 || metadataDecisions.length > 0 || removed.size > 0;
  if (reasons.length === 0 && preview.can_confirm !== true && !hasLocalResolution) {
    reasons.push("This preview has no pending changes that can be saved.");
  }
  return { visible: true, disabled: reasons.length > 0, reasons };
}

export function unresolvedPreviewCells(preview) {
  const cells = [];
  for (const row of preview?.rows || []) {
    const unresolved = new Set([...(row.unresolved_months || []), ...(row.missing_months || [])]);
    const invalid = new Set(row.invalid_months || []);
    for (const month of Object.keys(row.raw_amounts || row.amounts || {})) {
      if (!unresolved.has(month) && !invalid.has(month)) {
        continue;
      }
      cells.push({
        stage_id: row.stage_id,
        budget_code: row.budget_code,
        month,
        kind: invalid.has(month) ? "invalid" : "unresolved_blank",
        raw: row.raw_amounts?.[month],
      });
    }
  }
  return cells;
}

export function previewCellDisplay(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string" && value.trim() === "-") {
    return "-";
  }
  return value;
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
