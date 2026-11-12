import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import SummaryCard from "./SummaryCard";
import { getMasterDataset } from "../api/datasetApi";
import {
  cellClass,
  confirmationBlocked,
  reviewConfirmationState,
  displayAmount,
  unresolvedPreviewCells,
  previewCellDisplay,
} from "../utils/historicalWorkflow";
import { SAVE_UNKNOWN_MESSAGE, confirmButtonPresentation } from "../utils/settingsFlow";

function amountText(value) {
  if (value === "DECLARED_MISSING" || value === "-") {
    return "-";
  }
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  return displayAmount(value);
}

export function PreviewWorkspace({ preview, onStage, onConfirm, onAuthorize, saving, savePhase, staging, busy, onCheckStatus }) {
  const [rows, setRows] = useState(preview.rows || []);
  const [removed, setRemoved] = useState(preview.removed_stage_ids || []);
  const [editing, setEditing] = useState("");
  const [dirty, setDirty] = useState(false);
  const [cellFilter, setCellFilter] = useState("unresolved");
  const [missingDecisions, setMissingDecisions] = useState(preview.missing_decisions || []);
  const summary = preview.summary || {};
  const categoryChangeCount =
    summary.category_changes ??
    (preview.metadata_conflicts || []).filter((item) => item?.category_differs).length;

  useEffect(() => {
    setRows(preview.rows || []);
    setRemoved(preview.removed_stage_ids || []);
    setMissingDecisions(preview.missing_decisions || []);
    setDirty(false);
  }, [preview]);

  function updateRow(stageId, patch) {
    setDirty(true);
    setRows((current) => current.map((row) => (row.stage_id === stageId ? { ...row, ...patch } : row)));
  }

  function updateCell(stageId, month, value) {
    setDirty(true);
    setRows((current) =>
      current.map((row) => {
        if (row.stage_id !== stageId) {
          return row;
        }
        const raw_amounts = { ...(row.raw_amounts || row.amounts || {}), [month]: value };
        return { ...row, raw_amounts };
      })
    );
  }

  function reviewBody() {
    return {
      rows,
      removed_stage_ids: removed,
      missing_decisions: missingDecisions,
    };
  }

  async function saveChanges() {
    await onStage(reviewBody());
    setDirty(false);
  }

  const problemCells = useMemo(() => unresolvedPreviewCells({ ...preview, rows }), [preview, rows]);
  const filteredCells = useMemo(() => {
    if (cellFilter === "all") {
      return problemCells;
    }
    if (cellFilter === "invalid") {
      return problemCells.filter((cell) => cell.kind === "invalid");
    }
    return problemCells.filter((cell) => cell.kind === "unresolved_blank");
  }, [problemCells, cellFilter]);

  const reviewState = reviewConfirmationState(preview, {
    missingDecisions,
    removed,
    dirty,
  });
  const confirmPresentation = confirmButtonPresentation({
    phase: savePhase,
    blocked: reviewState.disabled || dirty || preview.can_confirm !== true,
    saving,
  });

  return (
    <div className="settings-preview">
      <p className="muted">{preview.forecast_note}</p>
      <p className="muted">
        Blank cells need a number, zero, a negative amount, or a standalone &quot;-&quot; for declared missing. Months left out of
        the file stay unchanged on the master. &quot;-&quot; is not a negative number.
      </p>
      <h3>Validation summary</h3>
      <div className="summary-grid settings-summary-grid">
        <SummaryCard label="File Structure" value={summary.file_structure || (confirmationBlocked(preview) ? "Needs correction" : "Valid")} />
        <SummaryCard label="Period Detected" value={summary.period_detected || "—"} />
        <SummaryCard label="Budget Codes Uploaded" value={summary.budget_codes_uploaded ?? "—"} />
        <SummaryCard label="Existing Codes" value={summary.existing_codes ?? "—"} />
        <SummaryCard label="New Budget Codes" value={summary.new_budget_codes ?? "—"} />
        <SummaryCard label="Categories Represented" value={summary.categories_represented ?? "—"} />
        <SummaryCard label="Valid Numeric Values" value={summary.valid_numeric_values ?? "—"} />
        <SummaryCard label="Unresolved Blanks" value={summary.unresolved_blank_cells ?? preview.unresolved_blank_count ?? 0} />
        <SummaryCard
          label="Declared Missing (-)"
          value={summary.declared_missing_cells ?? summary.missing_values ?? 0}
        />
        <SummaryCard label="Invalid Values" value={summary.invalid_values ?? preview.invalid_count ?? "—"} />
        <SummaryCard label="Duplicate Conflicts" value={summary.duplicate_conflicts ?? preview.duplicate_conflicts?.length ?? 0} />
      </div>
      <p className="muted">
        Missing Values means explicit &quot;-&quot; declarations in this upload only. Unresolved blanks and invalid text are counted
        separately and must be corrected before Confirm &amp; Save.
      </p>
      <h3>Master impact</h3>
      <div className="summary-grid settings-summary-grid">
        <SummaryCard label="Current Budget Codes" value={summary.current_budget_codes ?? "—"} />
        <SummaryCard label="After Save" value={summary.budget_codes_after_save ?? "—"} />
        <SummaryCard label="Current observed period" value={summary.current_observed_period || "—"} />
        <SummaryCard label="Latest observed historical month" value={summary.latest_observed_month_after_save || "—"} />
        <SummaryCard label="New Values" value={summary.new_values ?? preview.new_count ?? 0} />
        <SummaryCard label="Updated Values" value={summary.updated_values ?? preview.changed_count ?? 0} />
        <SummaryCard label="Unchanged Values" value={summary.unchanged_values ?? preview.unchanged_count ?? 0} />
        <SummaryCard label="Category Changes" value={categoryChangeCount} />
      </div>
      {categoryChangeCount > 0 ? (
        <p className="muted" role="status">
          {categoryChangeCount} budget code{categoryChangeCount === 1 ? "" : "s"} will use the uploaded category on Confirm
          &amp; Save. That category is applied across all saved historical months for each code. Account names keep the
          current master values for existing codes.
        </p>
      ) : null}
      {summary.unresolved_formula_cells ? (
        <p className="muted">
          {summary.unresolved_formula_cells} formula cells had no calculated number, including Excel #N/A. They were not imported, so the stored master value for each of those months stays unchanged.
        </p>
      ) : null}
      {preview.errors?.length || preview.structural_errors?.length || preview.cell_errors?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>Validation failed. Blocking issues must be resolved before Confirm & Save.</p>
          <ul>
            {[...(preview.errors || []), ...(preview.structural_errors || []), ...(preview.cell_errors || [])]
              .slice(0, 20)
              .map((item, index) => (
                <li key={`${item.row}-${item.message}-${index}`}>{item.message}</li>
              ))}
          </ul>
        </div>
      ) : null}
      {problemCells.length ? (
        <div className="settings-preview-fix-panel">
          <div className="settings-preview-fix-toolbar">
            <h3>Correct unresolved cells ({problemCells.length})</h3>
            <div className="settings-preview-fix-filters">
              <button
                type="button"
                className={cellFilter === "unresolved" ? "master-filter-button is-selected" : "master-filter-button"}
                onClick={() => setCellFilter("unresolved")}
              >
                Unresolved blanks
              </button>
              <button
                type="button"
                className={cellFilter === "invalid" ? "master-filter-button is-selected" : "master-filter-button"}
                onClick={() => setCellFilter("invalid")}
              >
                Invalid
              </button>
              <button
                type="button"
                className={cellFilter === "all" ? "master-filter-button is-selected" : "master-filter-button"}
                onClick={() => setCellFilter("all")}
              >
                All issues
              </button>
            </div>
          </div>
          <p className="muted">
            Edit here, then Save Changes to revalidate. Pending edits stay while you filter or scroll. Confirm stays disabled
            until the staged revision has no unresolved or invalid cells.
          </p>
          <div className="settings-preview-fix-scroll">
            <table className="settings-preview-fix-table">
              <thead>
                <tr>
                  <th>Budget Code</th>
                  <th>Month</th>
                  <th>Issue</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {filteredCells.map((cell) => {
                  const row = rows.find((item) => item.stage_id === cell.stage_id);
                  const current = previewCellDisplay(row?.raw_amounts?.[cell.month] ?? "");
                  return (
                    <tr
                      key={`${cell.stage_id}-${cell.month}`}
                      className={cell.kind === "invalid" ? "settings-cell-invalid" : "settings-cell-unresolved"}
                    >
                      <td>{cell.budget_code}</td>
                      <td>{cell.month}</td>
                      <td>{cell.kind === "invalid" ? "Invalid" : "Unresolved blank"}</td>
                      <td>
                        <input
                          aria-label={`${cell.budget_code} ${cell.month}`}
                          value={current}
                          placeholder="number or -"
                          onChange={(event) => updateCell(cell.stage_id, cell.month, event.target.value)}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {preview.duplicate_conflicts?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>Duplicate Budget Code and month conflicts are blocking confirmation. Edit or Remove a staged row. Confirm & Save sends those decisions and does not change the master until you confirm.</p>
          {preview.duplicate_conflicts.map((conflict) => (
            <div key={`${conflict.budget_code}-${conflict.month}`}>
              <p>
                {conflict.budget_code} · {conflict.month}
              </p>
              {(conflict.records || []).map((record) => (
                <div key={record.stage_id} className="settings-upload-row">
                  <span>
                    Row {record.source_row}: {amountText(record.amount)}
                  </span>
                  <button type="button" className="export-button-secondary" onClick={() => setEditing(record.stage_id)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="export-button-secondary"
                    disabled={busy}
                    onClick={() => {
                      Promise.resolve(onAuthorize?.()).then((allowed) => {
                        if (!allowed) {
                          return;
                        }
                        setDirty(true);
                        setRemoved((current) => (current.includes(record.stage_id) ? current : [...current, record.stage_id]));
                      });
                    }}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
      {preview.missing_conflicts?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>A blank upload cell overlaps a stored value. A blank does not declare the value missing.</p>
          <p className="muted">Keep existing keeps the stored number or declaration. Clear stored value removes the number and any declaration, so the code/month is not submitted. Declare missing records an explicit missing declaration and removes the number. Remove drops this blank cell and leaves the master unchanged.</p>
          {preview.missing_conflicts.map((item) => (
            <div key={`${item.budget_code}-${item.month}`} className="settings-upload-row">
              <span>
                {item.budget_code} {item.month}: master {amountText(item.current)}, upload missing
              </span>
              <button type="button" className="export-button-secondary" onClick={() => setMissingDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code || entry.month !== item.month), { budget_code: item.budget_code, month: item.month, action: "keep_master" }])}>
                Keep existing
              </button>
              <button type="button" className="export-button-secondary" onClick={() => setMissingDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code || entry.month !== item.month), { budget_code: item.budget_code, month: item.month, action: "clear" }])}>
                Clear stored value
              </button>
              <button type="button" className="export-button-secondary" onClick={() => setMissingDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code || entry.month !== item.month), { budget_code: item.budget_code, month: item.month, action: "declare_missing" }])}>
                Declare missing
              </button>
              <button
                type="button"
                className="export-button-secondary"
                disabled={busy}
                onClick={() => {
                  Promise.resolve(onAuthorize?.()).then((allowed) => {
                    if (!allowed) {
                      return;
                    }
                    setMissingDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code || entry.month !== item.month), { budget_code: item.budget_code, month: item.month, action: "remove_staged" }]);
                  });
                }}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      ) : null}
      {editing ? (
        <div className="table-wrap data-scroll">
          <table>
            <thead>
              <tr>
                <th>Budget Code</th>
                <th>Description</th>
                <th>Category</th>
                <th>Month</th>
                <th>Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {rows
                .filter((row) => row.stage_id === editing && !removed.includes(row.stage_id))
                .map((row) =>
                  Object.keys(row.raw_amounts || row.amounts || {}).map((month) => (
                    <tr key={`${row.stage_id}-${month}`}>
                      <td>{row.budget_code}</td>
                      <td>
                        <input value={row.description || ""} onChange={(event) => updateRow(row.stage_id, { description: event.target.value })} />
                      </td>
                      <td>
                        <input value={row.category || ""} onChange={(event) => updateRow(row.stage_id, { category: event.target.value })} />
                      </td>
                      <td>{month}</td>
                      <td>
                        <input
                          value={(row.raw_amounts || {})[month] ?? row.amounts?.[month] ?? ""}
                          onChange={(event) =>
                            updateRow(row.stage_id, {
                              raw_amounts: { ...(row.raw_amounts || row.amounts || {}), [month]: event.target.value },
                            })
                          }
                        />
                      </td>
                    </tr>
                  ))
                )}
            </tbody>
          </table>
        </div>
      ) : null}
      <div className="settings-upload-row">
        <button type="button" className="generate-button" onClick={saveChanges} disabled={staging || busy || !dirty}>
          {staging ? "Revalidating..." : dirty ? "Save Changes & Revalidate" : "No pending edits"}
        </button>
        <button type="button" className="export-button-secondary" onClick={() => setEditing("")}>
          Cancel row edit
        </button>
        {dirty ? <span className="muted">Staged edits are not confirmed until Save Changes succeeds.</span> : null}
      </div>
      {preview.merge_preview?.length ? (
        <div className="table-wrap data-scroll">
          <table>
            <thead>
              <tr>
                <th>Budget Code</th>
                <th>Month</th>
                <th>Current</th>
                <th>Uploaded</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {preview.merge_preview.map((item) => (
                <tr key={`${item.stage_id}-${item.budget_code}-${item.month}-${item.result}`}>
                  <td>{item.budget_code}</td>
                  <td>{item.month}</td>
                  <td>{amountText(item.current)}</td>
                  <td>{amountText(item.uploaded)}</td>
                  <td>{item.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {reviewState.visible ? (
        <div className="settings-confirm-bar">
          <h3>Save to historical master</h3>
          {savePhase === "unknown" ? (
            <p className="settings-confirm-note" role="status">{SAVE_UNKNOWN_MESSAGE}</p>
          ) : reviewState.disabled && savePhase !== "saved" && savePhase !== "saving" ? (
            <div className="settings-confirm-note" role="status">
              <p>Confirm & Save is disabled until every decision below is resolved. The historical master has not been changed.</p>
              <ul>
                {reviewState.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          ) : savePhase === "saved" ? (
            <p className="settings-confirm-note" role="status">Changes saved to the historical master successfully.</p>
          ) : (
            <p className="settings-confirm-note">
              Review the changes above. Confirm & Save writes them to the historical master once. Preview does not change the master.
            </p>
          )}
          <button
            type="button"
            className="generate-button"
            onClick={() => onConfirm(reviewBody())}
            disabled={confirmPresentation.disabled || busy}
          >
            {confirmPresentation.label}
          </button>
          {confirmPresentation.checkStatus ? (
            <button type="button" className="export-button-secondary" onClick={() => onCheckStatus()}>
              Check save status
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

const MASTER_FILTERS = [
  ["all", "All Master Data"],
  ["changes", "Changes from this Upload"],
  ["new", "New"],
  ["updated", "Updated"],
  ["missing", "Missing"],
];

const MASTER_CATEGORY_ALL = "";

function MasterToolbar({
  onView,
  onEdit,
  onChangeCategory,
  busy,
  editing = false,
  activeFilter,
  onFilter,
  selectedCount,
}) {
  return (
    <div className="master-dataset-toolbar">
      <button type="button" className="master-mode-button" onClick={onView}>
        View Master Dataset
      </button>
      <button type="button" className="master-mode-button" onClick={() => onEdit()} disabled={busy}>
        Edit Master Dataset
      </button>
      {editing && onChangeCategory ? (
        <button
          type="button"
          className="master-mode-button"
          onClick={() => onChangeCategory()}
          disabled={busy}
        >
          Change Category
        </button>
      ) : null}
      {onFilter
        ? MASTER_FILTERS.map(([filter, label]) => {
            const selected = filter === activeFilter;
            const count = selected && Number.isFinite(Number(selectedCount)) ? Number(selectedCount) : null;
            return (
              <button
                key={filter}
                type="button"
                className={selected ? "master-filter-button is-selected" : "master-filter-button"}
                aria-pressed={selected}
                onClick={() => onFilter(filter)}
              >
                <span className="master-filter-label">{label}</span>
                {count != null ? <span className="master-filter-count">{count}</span> : null}
              </button>
            );
          })
        : null}
    </div>
  );
}

function MasterCategoryFilter({
  categories = [],
  value = MASTER_CATEGORY_ALL,
  onChange,
  search = "",
  onSearch,
  matchingCount = null,
  disabled = false,
}) {
  const options = Array.isArray(categories) ? categories.filter((name) => String(name || "").trim()) : [];
  const selected = String(value || "");
  const showCount = selected && Number.isFinite(Number(matchingCount));
  return (
    <div className="master-table-filters">
      <div className="master-category-filter">
        <label htmlFor="master-category-filter">Category</label>
        <div className="master-category-filter-controls">
          <select
            id="master-category-filter"
            value={selected}
            disabled={disabled}
            onChange={(event) => onChange?.(event.target.value)}
            aria-describedby="master-category-filter-note"
          >
            <option value={MASTER_CATEGORY_ALL}>All Categories</option>
            {options.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          {showCount ? (
            <span className="master-category-filter-count" id="master-category-filter-count">
              {Number(matchingCount)} matching Budget Code{Number(matchingCount) === 1 ? "" : "s"}
            </span>
          ) : (
            <span className="muted" id="master-category-filter-note">
              Display filter only — does not reassign categories.
            </span>
          )}
        </div>
      </div>
      <div className="master-code-search">
        <label htmlFor="master-code-search">Search Budget Code</label>
        <input
          id="master-code-search"
          type="search"
          value={search}
          disabled={disabled}
          placeholder="Budget code or account name"
          onChange={(event) => onSearch?.(event.target.value)}
        />
      </div>
    </div>
  );
}

function ChangeCategoryDialog({
  open,
  categories = [],
  onCancel,
  onSave,
  saving = false,
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [selected, setSelected] = useState(null);
  const [nextCategory, setNextCategory] = useState("");
  const [saveError, setSaveError] = useState("");
  const searchBoxRef = useRef(null);
  const requestId = useRef(0);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    setQuery("");
    setResults([]);
    setSearching(false);
    setSearchError("");
    setSelected(null);
    setNextCategory("");
    setSaveError("");
    const timer = window.setTimeout(() => searchBoxRef.current?.focus(), 40);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const needle = String(query || "").trim();
    if (needle.length < 1) {
      setResults([]);
      setSearching(false);
      setSearchError("");
      return undefined;
    }
    const current = ++requestId.current;
    setSearching(true);
    setSearchError("");
    const timer = window.setTimeout(() => {
      // Search the full active master — ignore table page, category filter, and tab.
      getMasterDataset({
        page: 1,
        pageSize: 50,
        search: needle,
        revisionFilter: "all",
      })
        .then((payload) => {
          if (current !== requestId.current) {
            return;
          }
          setResults(Array.isArray(payload?.rows) ? payload.rows : []);
          setSearching(false);
        })
        .catch((cause) => {
          if (current !== requestId.current) {
            return;
          }
          setResults([]);
          setSearching(false);
          setSearchError(cause.message || "Budget codes could not be searched.");
        });
    }, 220);
    return () => window.clearTimeout(timer);
  }, [open, query]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    function onKeyDown(event) {
      if (event.key === "Escape" && !saving) {
        event.preventDefault();
        onCancel?.();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, saving, onCancel]);

  if (!open || typeof document === "undefined") {
    return null;
  }

  const options = Array.isArray(categories)
    ? categories.filter(
        (name) => String(name || "").trim() && String(name).trim() !== String(selected?.category || "").trim(),
      )
    : [];
  const canSave =
    Boolean(selected?.budget_code) &&
    Boolean(String(nextCategory || "").trim()) &&
    String(nextCategory).trim() !== String(selected?.category || "").trim() &&
    !saving;

  async function handleSave() {
    if (!canSave || !onSave) {
      return;
    }
    setSaveError("");
    const outcome = await onSave({
      budget_code: selected.budget_code,
      description: selected.description || "",
      current_category: selected.category || "",
      new_category: nextCategory,
    });
    if (outcome === true || outcome?.ok === true) {
      return;
    }
    setSaveError(
      (typeof outcome === "object" && outcome?.error) ||
        "The category could not be changed. Nothing was saved.",
    );
  }

  return createPortal(
    <div className="change-category-backdrop" role="presentation">
      <article
        className="card change-category-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="change-category-title"
      >
        <div className="change-category-body">
          <h2 id="change-category-title">Change Category</h2>
          <label className="change-category-field-label" htmlFor="change-category-code-search">
            Budget Code
          </label>
          <input
            ref={searchBoxRef}
            id="change-category-code-search"
            className="change-category-search"
            type="search"
            value={query}
            disabled={saving}
            placeholder="Search by budget code or account name"
            autoComplete="off"
            aria-controls="change-category-results"
            aria-expanded={Boolean(query.trim())}
            onChange={(event) => {
              setQuery(event.target.value);
              setSelected(null);
              setNextCategory("");
              setSaveError("");
            }}
          />
          {query.trim() ? (
            <div id="change-category-results" className="change-category-results" role="listbox" aria-label="Matching budget codes">
              {searching ? <p className="muted">Searching the active master…</p> : null}
              {searchError ? (
                <p className="change-category-inline-error" role="alert">
                  {searchError}
                </p>
              ) : null}
              {!searching && !searchError && results.length === 0 ? (
                <p className="muted">No budget codes match “{query.trim()}”.</p>
              ) : null}
              {!searching
                ? results.map((row) => {
                    const active = selected?.budget_code === row.budget_code;
                    return (
                      <button
                        key={row.budget_code}
                        type="button"
                        role="option"
                        aria-selected={active}
                        className={active ? "change-category-result is-selected" : "change-category-result"}
                        disabled={saving}
                        onClick={() => {
                          setSelected(row);
                          setNextCategory("");
                          setSaveError("");
                        }}
                      >
                        <span className="change-category-result-code">{row.budget_code}</span>
                        <span className="change-category-result-name">{row.description || "—"}</span>
                        <span className="change-category-result-category">{row.category || "—"}</span>
                      </button>
                    );
                  })
                : null}
            </div>
          ) : (
            <p className="muted change-category-hint">Search the full active master. Table filters and paging do not apply here.</p>
          )}
          {selected ? (
            <>
              <dl className="change-category-summary">
                <div>
                  <dt>Budget Code</dt>
                  <dd>{selected.budget_code}</dd>
                </div>
                <div>
                  <dt>Account Name</dt>
                  <dd>{selected.description || "—"}</dd>
                </div>
                <div>
                  <dt>Current Category</dt>
                  <dd>{selected.category || "—"}</dd>
                </div>
              </dl>
              <label className="change-category-field-label" htmlFor="change-category-select">
                New Category
              </label>
              <select
                id="change-category-select"
                className="change-category-select"
                value={nextCategory}
                disabled={saving}
                onChange={(event) => {
                  setNextCategory(event.target.value);
                  setSaveError("");
                }}
              >
                <option value="">Select a category</option>
                {options.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <p className="muted change-category-helper">
                This move applies to all historical months and forecast values for this Budget Code. Monthly amounts,
                missing markers, forecast eligibility, and Book Entry exclusions stay the same. Trained models are not
                retrained.
              </p>
            </>
          ) : null}
          {saveError ? (
            <p className="change-category-inline-error" role="alert">
              {saveError}
            </p>
          ) : null}
        </div>
        <div className="change-category-footer">
          <button type="button" className="export-button-secondary" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="generate-button" onClick={handleSave} disabled={!canSave}>
            {saving ? "Saving..." : "Save Category"}
          </button>
        </div>
      </article>
    </div>,
    document.body,
  );
}

function MasterPagination({ master, rows, hasCategoryFilter, hasSearch, category, search, onPage }) {
  const total = Number(master.total_codes) || 0;
  const pageSize = Math.max(1, Number(master.page_size) || 20);
  const page = Math.max(1, Number(master.page) || 1);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const singular = total === 1;
  const countLabel = `${total} matching budget code${singular ? "" : "s"}`;
  const filterNote = [
    hasCategoryFilter ? `in ${category}` : "",
    hasSearch ? `matching “${search}”` : "",
  ]
    .filter(Boolean)
    .join(" ");

  if (totalPages <= 1) {
    return (
      <div className="master-pagination-row" role="status">
        <span className="master-result-count">
          {countLabel}
          {filterNote ? ` ${filterNote}` : ""}
        </span>
      </div>
    );
  }

  return (
    <div className="master-pagination-row">
      <span className="master-result-count">
        {countLabel}
        {filterNote ? ` ${filterNote}` : ""}
      </span>
      <div className="master-pagination-controls">
        <button
          type="button"
          className="master-page-button"
          onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page <= 1 || !rows.length}
        >
          Previous
        </button>
        <span className="master-page-status">
          Page {page} of {totalPages}
        </span>
        <button
          type="button"
          className="master-page-button"
          onClick={() => onPage(page + 1)}
          disabled={!rows.length || page >= totalPages}
        >
          Next
        </button>
      </div>
    </div>
  );
}

export function MasterSection({
  master,
  editing,
  draft,
  onView,
  onEdit,
  onDraft,
  onPreview,
  onConfirm,
  onFilter,
  onPage,
  onCategory,
  onSearch,
  onChangeCategory,
  category = MASTER_CATEGORY_ALL,
  search = "",
  preview,
  confirming,
  busy,
  activeFilter = "all",
  categorySaving = false,
}) {
  const [categoryDialogOpen, setCategoryDialogOpen] = useState(false);

  if (!master) {
    return <MasterToolbar onView={onView} onEdit={onEdit} busy={busy} editing={false} />;
  }
  const categories = Array.isArray(master.categories) ? master.categories : [];
  const rows = Array.isArray(master.rows) ? master.rows : [];
  const hasCategoryFilter = Boolean(String(category || "").trim());
  const hasSearch = Boolean(String(search || "").trim());

  function closeChangeCategory() {
    if (categorySaving) {
      return;
    }
    setCategoryDialogOpen(false);
  }

  async function saveChangeCategory(request) {
    if (!onChangeCategory) {
      return { ok: false, error: "Category changes are not available." };
    }
    const outcome = await onChangeCategory(request);
    if (outcome === true || outcome?.ok === true) {
      setCategoryDialogOpen(false);
      return outcome === true ? { ok: true } : outcome;
    }
    return typeof outcome === "object" && outcome
      ? outcome
      : { ok: false, error: "The category could not be changed." };
  }

  return (
    <div className="master-dataset-section">
      {editing ? (
        <p className="muted">
          Leave a cell blank to clear the stored value. That does not declare it missing. Type - to declare it missing.
        </p>
      ) : (
        <p className="muted">{master.actuals_display_label || "Historical actuals are shown with their original sign. Stored values are unchanged."}</p>
      )}
      <MasterToolbar
        onView={onView}
        onEdit={onEdit}
        onChangeCategory={onChangeCategory ? () => setCategoryDialogOpen(true) : undefined}
        busy={busy || categorySaving}
        editing={editing}
        activeFilter={activeFilter}
        onFilter={onFilter}
        selectedCount={master.total_codes}
      />
      <MasterCategoryFilter
        categories={categories}
        value={category}
        onChange={onCategory}
        search={search}
        onSearch={onSearch}
        matchingCount={hasCategoryFilter || hasSearch ? master.total_codes : null}
        disabled={busy}
      />
      {rows.length ? (
        <div className="table-wrap settings-wide-table data-scroll data-scroll-lg">
          <table>
            <thead>
              <tr>
                <th>Budget Code</th>
                <th>Category</th>
                {(master.months || []).map((month) => (
                  <th key={month}>{month}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.budget_code}>
                  <td>{row.budget_code}</td>
                  <td>{row.category}</td>
                  {(master.months || []).map((month) => {
                    const stored = row.raw_amounts?.[month] ?? row.amounts?.[month];
                    const display = row.display_amounts?.[month] ?? stored;
                    const declared = row.submissions?.[month] === "declared_missing";
                    const shown = declared ? "-" : (editing ? (stored ?? "") : (display ?? ""));
                    const highlight = row.highlights?.[month];
                    const storedTitle = !editing && stored != null && Number(stored) < 0 ? `Stored value ${stored}` : undefined;
                    return (
                      <td key={month} className={cellClass(highlight)} title={storedTitle}>
                        {editing ? (
                          <input
                            value={draft[`${row.budget_code}|${month}`] ?? shown}
                            onChange={(event) => onDraft(row, month, event.target.value)}
                            aria-label={`${row.budget_code} ${month}`}
                          />
                        ) : declared ? (
                          "-"
                        ) : (
                          amountText(display)
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted master-category-empty" role="status">
          {hasCategoryFilter || hasSearch
            ? "No Budget Codes match the current category, search, and tab filters."
            : "No Budget Codes match the current tab."}
        </p>
      )}
      <MasterPagination
        master={master}
        rows={rows}
        hasCategoryFilter={hasCategoryFilter}
        hasSearch={hasSearch}
        category={category}
        search={search}
        onPage={onPage}
      />
      {editing ? (
        <div className="master-edit-actions">
          <button type="button" className="master-edit-cancel" onClick={onView} disabled={busy || confirming}>
            Cancel
          </button>
          <button
            type="button"
            className="export-button-secondary"
            onClick={() => onPreview()}
            disabled={busy || confirming || categorySaving}
          >
            Save Changes
          </button>
          <button
            type="button"
            className="generate-button"
            onClick={() => onConfirm()}
            disabled={!preview?.can_confirm || confirming || busy}
          >
            {confirming ? "Saving..." : "Confirm Changes"}
          </button>
        </div>
      ) : null}
      {preview?.preview?.length ? (
        <div className="table-wrap data-scroll">
          <table>
            <thead>
              <tr>
                <th>Budget Code</th>
                <th>Month</th>
                <th>Current Value</th>
                <th>Proposed Value</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {preview.preview.map((item) => (
                <tr key={`${item.budget_code}-${item.month}`}>
                  <td>{item.budget_code}</td>
                  <td>{item.month}</td>
                  <td>{amountText(item.current)}</td>
                  <td>{amountText(item.proposed)}</td>
                  <td>{item.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {preview?.errors?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>Validation failed.</p>
          <ul>
            {preview.errors.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <ChangeCategoryDialog
        open={categoryDialogOpen}
        categories={categories}
        onCancel={closeChangeCategory}
        onSave={saveChangeCategory}
        saving={categorySaving}
      />
    </div>
  );
}

export function RollbackDialog({ revision, reason, onReason, review, onReview, onConfirm, onCancel, reviewing }) {
  return (
    <div className="settings-dialog-backdrop">
      <article className="card settings-dialog" role="dialog" aria-modal="true">
        <h2>Rollback Upload</h2>
        <p className="muted">This reverses safe master changes and keeps the original upload and audit history.</p>
        <label htmlFor="rollback-reason">Reason for rollback *</label>
        <textarea id="rollback-reason" value={reason} onChange={(event) => onReason(event.target.value)} required />
        <div className="settings-upload-row">
          <button type="button" className="export-button-secondary" onClick={() => onReview()} disabled={!reason.trim() || reviewing}>
            Review Rollback
          </button>
          <button type="button" className="export-button-secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
        {review ? (
          <div>
            <p>
              Safe removals {review.safe_removals}. Previous values to restore {review.previous_values_to_restore}. Later-revision conflicts {review.later_revision_conflicts}.
            </p>
            <div className="table-wrap data-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Budget Code</th>
                    <th>Month</th>
                    <th>Before Upload</th>
                    <th>Value Introduced by Upload</th>
                    <th>Current Master Value</th>
                    <th>Proposed Rollback</th>
                    <th>Conflict Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(review.safe || []), ...(review.conflicts || [])].map((item) => (
                    <tr key={`${item.budget_code}-${item.month}`}>
                      <td>{item.budget_code}</td>
                      <td>{item.month}</td>
                      <td>{amountText(item.before_upload)}</td>
                      <td>{amountText(item.value_introduced)}</td>
                      <td>{amountText(item.current_master_value)}</td>
                      <td>{amountText(item.proposed_rollback)}</td>
                      <td>{item.conflict_status === "later_revision_conflict" ? "LATER REVISION CONFLICT" : "Safe"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button type="button" className="generate-button" onClick={() => onConfirm()} disabled={!review.safe?.length || reviewing}>
              Confirm Rollback
            </button>
          </div>
        ) : null}
        <p className="muted">{revision?.file_name}</p>
      </article>
    </div>
  );
}

export function RevisionDetail({ detail, onClose }) {
  const revision = detail.revision || detail.file || {};
  const changes = detail.changes || [];
  return (
    <article className="card section-card">
      <div className="settings-panel-header">
        <h2>View Revision</h2>
        <button type="button" className="export-button-secondary" onClick={onClose}>
          Cancel
        </button>
      </div>
      <p>
        {revision.file_name} · {revision.change_kind || "UPLOAD"} · {revision.uploaded_by} · {revision.status}
      </p>
      <div className="table-wrap data-scroll">
        <table>
          <thead>
            <tr>
              <th>Budget Code</th>
              <th>Month</th>
              <th>Operation</th>
              <th>Previous</th>
              <th>New</th>
            </tr>
          </thead>
          <tbody>
            {changes.map((item) => (
              <tr key={item.id || `${item.budget_code}-${item.month}-${item.operation}`}>
                <td>{item.budget_code}</td>
                <td>{item.month}</td>
                <td>{item.operation}</td>
                <td>{amountText(item.previous_amount)}</td>
                <td>{amountText(item.new_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
