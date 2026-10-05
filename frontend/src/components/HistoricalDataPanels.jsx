import { useEffect, useState } from "react";
import SummaryCard from "./SummaryCard";
import { cellClass, confirmationBlocked, displayAmount } from "../utils/historicalWorkflow";

function amountText(value) {
  return displayAmount(value);
}

export function PreviewWorkspace({ preview, onStage, onConfirm, onAuthorize, saving, staging, busy }) {
  const [rows, setRows] = useState(preview.rows || []);
  const [removed, setRemoved] = useState([]);
  const [editing, setEditing] = useState("");
  const [missingDecisions, setMissingDecisions] = useState([]);
  const [metadataDecisions, setMetadataDecisions] = useState([]);
  const summary = preview.summary || {};

  useEffect(() => {
    setRows(preview.rows || []);
    setRemoved(preview.removed_stage_ids || []);
    setMissingDecisions(preview.missing_decisions || []);
    setMetadataDecisions(preview.metadata_decisions || []);
  }, [preview]);

  function updateRow(stageId, patch) {
    setRows((current) => current.map((row) => (row.stage_id === stageId ? { ...row, ...patch } : row)));
  }

  async function saveChanges() {
    await onStage({
      rows,
      removed_stage_ids: removed,
      missing_decisions: missingDecisions,
      metadata_decisions: metadataDecisions,
    });
  }

  return (
    <div className="settings-preview">
      <p className="muted">{preview.forecast_note}</p>
      <h3>Validation summary</h3>
      <div className="summary-grid settings-summary-grid">
        <SummaryCard label="File Structure" value={summary.file_structure || (confirmationBlocked(preview) ? "Needs correction" : "Valid")} />
        <SummaryCard label="Period Detected" value={summary.period_detected || "—"} />
        <SummaryCard label="Budget Codes Uploaded" value={summary.budget_codes_uploaded ?? "—"} />
        <SummaryCard label="Existing Codes" value={summary.existing_codes ?? "—"} />
        <SummaryCard label="New Budget Codes" value={summary.new_budget_codes ?? "—"} />
        <SummaryCard label="Categories Represented" value={summary.categories_represented ?? "—"} />
        <SummaryCard label="Valid Numeric Values" value={summary.valid_numeric_values ?? "—"} />
        <SummaryCard label="Missing Values" value={summary.missing_values ?? "—"} />
        <SummaryCard label="Invalid Values" value={summary.invalid_values ?? preview.invalid_count ?? "—"} />
        <SummaryCard label="Duplicate Conflicts" value={summary.duplicate_conflicts ?? preview.duplicate_conflicts?.length ?? 0} />
      </div>
      <h3>Master impact</h3>
      <div className="summary-grid settings-summary-grid">
        <SummaryCard label="Current Budget Codes" value={summary.current_budget_codes ?? "—"} />
        <SummaryCard label="After Save" value={summary.budget_codes_after_save ?? "—"} />
        <SummaryCard label="Current observed period" value={summary.current_observed_period || "—"} />
        <SummaryCard label="Latest observed historical month" value={summary.latest_observed_month_after_save || "—"} />
        <SummaryCard label="New Values" value={summary.new_values ?? preview.new_count ?? 0} />
        <SummaryCard label="Updated Values" value={summary.updated_values ?? preview.changed_count ?? 0} />
        <SummaryCard label="Unchanged Values" value={summary.unchanged_values ?? preview.unchanged_count ?? 0} />
      </div>
      {preview.errors?.length || preview.structural_errors?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>Validation failed. Blocking issues must be resolved before Confirm & Save.</p>
          <ul>
            {(preview.structural_errors?.length ? preview.structural_errors : preview.errors).slice(0, 20).map((item, index) => (
              <li key={`${item.row}-${item.message}-${index}`}>{item.message}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview.duplicate_conflicts?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>Duplicate Budget Code and month conflicts are blocking confirmation. Edit or Remove a staged row, then Save Changes.</p>
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
      {preview.metadata_conflicts?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>Budget Code metadata conflicts need an explicit decision.</p>
          {preview.metadata_conflicts.map((item) => (
            <div key={item.budget_code} className="settings-upload-row">
              <span>
                {item.budget_code}: master {item.current_category} / {item.current_description}; upload {item.uploaded_category} / {item.uploaded_description}
              </span>
              <button type="button" className="export-button-secondary" onClick={() => setMetadataDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code), { budget_code: item.budget_code, action: "keep_master" }])}>
                Keep master
              </button>
              <button type="button" className="export-button-secondary" onClick={() => setMetadataDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code), { budget_code: item.budget_code, action: "accept_upload" }])}>
                Accept upload
              </button>
            </div>
          ))}
        </div>
      ) : null}
      {preview.missing_conflicts?.length ? (
        <div className="settings-ops-alert" role="alert">
          <p>A missing upload value would erase an existing master actual. Choose what to keep.</p>
          {preview.missing_conflicts.map((item) => (
            <div key={`${item.budget_code}-${item.month}`} className="settings-upload-row">
              <span>
                {item.budget_code} {item.month}: master {amountText(item.current)}, upload missing
              </span>
              <button type="button" className="export-button-secondary" onClick={() => setMissingDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code || entry.month !== item.month), { budget_code: item.budget_code, month: item.month, action: "keep_master" }])}>
                Keep existing
              </button>
              <button type="button" className="export-button-secondary" onClick={() => setMissingDecisions((current) => [...current.filter((entry) => entry.budget_code !== item.budget_code || entry.month !== item.month), { budget_code: item.budget_code, month: item.month, action: "clear" }])}>
                Replace with missing
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
        <button type="button" className="export-button-secondary" onClick={saveChanges} disabled={staging || busy}>
          {staging ? "Saving..." : "Save Changes"}
        </button>
        <button type="button" className="export-button-secondary" onClick={() => setEditing("")}>
          Cancel
        </button>
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
      <button type="button" className="generate-button" onClick={onConfirm} disabled={confirmationBlocked(preview) || saving || busy}>
        {saving ? "Saving..." : "Confirm & Save"}
      </button>
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

function MasterToolbar({ onView, onEdit, busy, activeFilter, onFilter, selectedCount }) {
  return (
    <div className="master-dataset-toolbar">
      <button type="button" className="master-mode-button" onClick={onView}>
        View Master Dataset
      </button>
      <button type="button" className="master-mode-button" onClick={onEdit} disabled={busy}>
        Edit Master Dataset
      </button>
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
                {label}
                {count != null ? <span className="master-filter-count"> {count}</span> : null}
              </button>
            );
          })
        : null}
    </div>
  );
}

export function MasterSection({ master, editing, draft, onView, onEdit, onDraft, onPreview, onConfirm, onFilter, onPage, preview, confirming, busy, activeFilter = "all" }) {
  if (!master) {
    return <MasterToolbar onView={onView} onEdit={onEdit} busy={busy} />;
  }
  return (
    <div>
      <MasterToolbar
        onView={onView}
        onEdit={onEdit}
        busy={busy}
        activeFilter={activeFilter}
        onFilter={onFilter}
        selectedCount={master.total_codes}
      />
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
            {(master.rows || []).map((row) => (
              <tr key={row.budget_code}>
                <td>{row.budget_code}</td>
                <td>{row.category}</td>
                {(master.months || []).map((month) => {
                  const value = row.amounts?.[month];
                  const highlight = row.highlights?.[month];
                  return (
                    <td key={month} className={cellClass(highlight)}>
                      {editing ? (
                        <input
                          value={draft[`${row.budget_code}|${month}`] ?? (value ?? "")}
                          onChange={(event) => onDraft(row, month, event.target.value)}
                        />
                      ) : (
                        amountText(value)
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="settings-pagination">
        <button type="button" onClick={() => onPage(Math.max(1, master.page - 1))} disabled={master.page <= 1}>
          Previous
        </button>
        <span>
          Page {master.page} · {master.total_codes} Budget Codes
        </span>
        <button type="button" onClick={() => onPage(master.page + 1)} disabled={master.page * master.page_size >= master.total_codes}>
          Next
        </button>
      </div>
      {editing ? (
        <div className="settings-upload-row">
          <button type="button" className="export-button-secondary" onClick={onPreview} disabled={busy}>
            Save Changes
          </button>
          <button type="button" className="generate-button" onClick={onConfirm} disabled={!preview?.can_confirm || confirming || busy}>
            {confirming ? "Saving..." : "Confirm Changes"}
          </button>
          <button type="button" className="export-button-secondary" onClick={onView}>
            Cancel
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
          <button type="button" className="export-button-secondary" onClick={onReview} disabled={!reason.trim() || reviewing}>
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
            <button type="button" className="generate-button" onClick={onConfirm} disabled={!review.safe?.length || reviewing}>
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
