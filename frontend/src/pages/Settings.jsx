import { useCallback, useEffect, useRef, useState } from "react";
import {
  confirmDataset,
  confirmMasterEdits,
  confirmRollback,
  downloadDatasetFile,
  getDatasetSummary,
  getMasterDataset,
  listRevisions,
  previewDataset,
  previewMasterEdits,
  previewRollback,
  stageDatasetPreview,
  viewRevision,
  assertEditorAccess,
} from "../api/datasetApi";
import { claimReturnedActionToken, consumeSettingsActionResume, requestSettingsAction, subscribeActionPrompt, takeActionFailure, takeReturnedActionExpiry } from "../auth/actionAuthentication";
import { getModelStatus, startRetrain } from "../api/retrainApi";
import { MasterSection, PreviewWorkspace, RevisionDetail, RollbackDialog } from "../components/HistoricalDataPanels";
import SummaryCard from "../components/SummaryCard";
import { formatHistoryPeriod } from "../utils/historyPeriod";
import { CONFIRM_TEXT } from "../utils/retrainWorkflow";
import {
  FILE_AUTH_DENIED,
  beginFileAuthentication,
  fileAuthenticationFailed,
  fileAuthenticationSucceeded,
  fileSelectionClick,
  initialFileSelection,
  subscribeFileSelectionReset,
} from "../utils/fileSelectionAuth";
import { isAuthorizationCancellation } from "../utils/settingsActions";

function formatStamp(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function OperationalAlert({ message, onRetry }) {
  if (!message) {
    return null;
  }
  return (
    <div className="settings-ops-alert" role="alert">
      <p>{message}</p>
      {onRetry ? (
        <button type="button" className="export-button-secondary" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export default function Settings() {
  const fileInputRef = useRef(null);
  const loadInFlight = useRef(false);
  const actionGuard = useRef({ pending: false });
  const [summary, setSummary] = useState(null);
  const [files, setFiles] = useState([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [error, setError] = useState("");
  const [retrainConfirm, setRetrainConfirm] = useState(false);
  const [retrainStarting, setRetrainStarting] = useState(false);
  const [modelStatus, setModelStatus] = useState(null);
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sheetName, setSheetName] = useState("");
  const [viewer, setViewer] = useState(null);
  const [master, setMaster] = useState(null);
  const [masterEditing, setMasterEditing] = useState(false);
  const [masterDraft, setMasterDraft] = useState({});
  const [masterPreview, setMasterPreview] = useState(null);
  const [masterFilter, setMasterFilter] = useState("all");
  const [masterHighlight, setMasterHighlight] = useState("");
  const [staging, setStaging] = useState(false);
  const [confirmingMaster, setConfirmingMaster] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState(null);
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackReview, setRollbackReview] = useState(null);
  const [reviewingRollback, setReviewingRollback] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [fileAuth, setFileAuth] = useState(initialFileSelection);
  const fileAuthRef = useRef(fileAuth);

  function updateFileAuth(next) {
    fileAuthRef.current = next;
    setFileAuth(next);
  }

  const validateDisabled = !file || previewing || actionPending;

  function protect(kind, payload, execute, options = {}) {
    const token = claimReturnedActionToken();
    const expiresAt = token ? takeReturnedActionExpiry() : 0;
    if (!token) {
      if (actionGuard.current.pending) {
        return Promise.resolve(false);
      }
      actionGuard.current.pending = true;
      setActionPending(true);
      requestSettingsAction({ kind, payload, destructive: Boolean(options.destructive) });
      return Promise.resolve(false);
    }
    setActionPending(true);
    return Promise.resolve()
      .then(() => execute(token, expiresAt))
      .then(() => true)
      .catch((cause) => {
        if (!isAuthorizationCancellation(cause)) {
          setError(cause.message || "The action was not authorized.");
        }
        return false;
      })
      .finally(() => setActionPending(false));
  }

  const load = useCallback(async () => {
    if (loadInFlight.current) {
      return;
    }
    loadInFlight.current = true;
    setLoading(true);
    setError("");
    try {
      const summaryPayload = await getDatasetSummary();
      const filesPayload = await listRevisions(page, 10);
      setLoadFailed(false);
      setSummary(summaryPayload);
      setFiles(filesPayload.revisions || []);
      setTotal(filesPayload.total || 0);
    } catch (cause) {
      setLoadFailed(true);
      setSummary(null);
      setFiles([]);
      setTotal(0);
      setError(cause.message || "Unable to load historical data.");
    } finally {
      setLoading(false);
      loadInFlight.current = false;
    }
  }, [page]);

  useEffect(() => subscribeActionPrompt((prompt) => {
    if (!prompt) {
      actionGuard.current.pending = false;
      setActionPending(false);
      const failure = takeActionFailure();
      if (failure?.kind === "choose-file") {
        updateFileAuth(fileAuthenticationFailed(failure.message));
        setError(failure.message);
      }
    }
  }), []);

  useEffect(() => subscribeFileSelectionReset(() => {
    updateFileAuth(initialFileSelection());
    setFile(null);
  }), []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const outcome = consumeSettingsActionResume(undefined, new Set([
      "choose-file",
      "edit-master",
      "open-rollback",
      "ask-retrain",
      "begin-retrain",
      "confirm-dataset",
      "stage-preview",
      "preview-master",
      "confirm-master",
      "review-rollback",
      "commit-rollback",
      "preview-dataset",
      "remove-stage",
    ]));
    if (!outcome || outcome.mode === "drop") {
      return;
    }
    if (outcome.mode === "restore") {
      restoreActionConfirmation(outcome.resume);
      return;
    }
    resumeAction(outcome.resume);
  }, []);

  useEffect(() => {
    let stop = false;
    getModelStatus()
      .then((payload) => {
        if (!stop) {
          setModelStatus(payload);
        }
      })
      .catch(() => {
        if (!stop) {
          setModelStatus({
            status: "unknown",
            published_at: null,
            message: "Model publication metadata is unavailable.",
            retraining_required: false,
          });
        }
      });
    return () => {
      stop = true;
    };
  }, [notice]);

  function handlePreview(nextSheet) {
    if (!file) {
      return Promise.resolve(false);
    }
    setError("");
    setNotice("");
    return protect("preview-dataset", { sheetName: nextSheet || sheetName || "" }, async (token) => {
      setPreviewing(true);
      try {
        const payload = await previewDataset(file, nextSheet || sheetName || undefined, token);
        setPreview(payload);
        if (payload.selected_sheet) {
          setSheetName(payload.selected_sheet);
        }
      } catch (cause) {
        if (!isAuthorizationCancellation(cause)) {
          setPreview(null);
          throw cause;
        }
      } finally {
        setPreviewing(false);
      }
    }, { destructive: true });
  }

  function chooseFile() {
    const attempt = beginFileAuthentication(fileAuthRef.current);
    if (!attempt.started) {
      return Promise.resolve(false);
    }
    setError("");
    updateFileAuth(attempt.state);
    return protect("choose-file", {}, async (token, expiresAt) => {
      try {
        await assertEditorAccess(token);
      } catch (cause) {
        const message = cause.message || FILE_AUTH_DENIED;
        updateFileAuth(fileAuthenticationFailed(message));
        throw cause;
      }
      const granted = fileAuthenticationSucceeded(expiresAt, Date.now());
      updateFileAuth(granted);
      if (!granted.selectEnabled) {
        throw new Error(granted.message);
      }
    });
  }

  function selectAuthorizedFile() {
    const result = fileSelectionClick(fileAuthRef.current, Date.now());
    updateFileAuth(result.state);
    if (!result.openPicker) {
      if (result.state.message && !result.state.selectEnabled) {
        setError(result.state.message);
      }
      return;
    }
    fileInputRef.current?.click();
  }

  async function handleSave(previewId = preview?.preview_id) {
    if (!previewId) {
      claimReturnedActionToken();
      return;
    }
    setError("");
    await protect("confirm-dataset", { previewId }, async (token) => {
      setSaving(true);
      try {
        const result = await confirmDataset(previewId, token);
        setNotice(result.message || "Historical dataset updated successfully.");
        setMasterHighlight(result.file_id || "");
        setMasterFilter("changes");
        setPreview(null);
        setFile(null);
        updateFileAuth(initialFileSelection());
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
        await load();
        await refreshMaster(1, "changes", result.file_id);
      } finally {
        setSaving(false);
      }
    }, { destructive: true });
  }

  async function openView(record) {
    setError("");
    try {
      const payload = await viewRevision(record.id);
      setViewer(payload);
    } catch (cause) {
      setError(cause.message || "Unable to open the saved file.");
    }
  }

  async function refreshMaster(page = 1, filter = masterFilter, highlight = masterHighlight, options = {}) {
    const payload = await getMasterDataset({ page, pageSize: 20, revisionFilter: filter, highlight });
    setMaster(payload);
    if (!options.keepEditing) {
      setMasterEditing(false);
      setMasterDraft({});
      setMasterPreview(null);
    }
  }

  function stagePreview(body, previewId = preview?.preview_id) {
    setError("");
    return protect("stage-preview", { previewId, body }, async (token) => {
      setStaging(true);
      try {
        const payload = await stageDatasetPreview(previewId, body, token);
        setPreview(payload);
      } finally {
        setStaging(false);
      }
    }, { destructive: true });
  }

  function draftMasterCell(row, month, value) {
    setMasterDraft((current) => ({ ...current, [`${row.budget_code}|${month}`]: value }));
  }

  function masterEdits() {
    return Object.entries(masterDraft).map(([key, value]) => {
      const [budgetCode, month] = key.split("|");
      const row = (master?.rows || []).find((item) => item.budget_code === budgetCode);
      return {
        budget_code: budgetCode,
        month,
        amount: value === "" ? null : value,
        expected_amount: row?.amounts?.[month] ?? null,
        description: row?.description || "",
        category: row?.category || "",
      };
    });
  }

  function previewMaster(edits = masterEdits(), version = master?.master_version) {
    setError("");
    return protect("preview-master", { edits, version }, async (token) => {
      const payload = await previewMasterEdits(edits, version, token);
      setMasterPreview(payload);
    }, { destructive: true });
  }

  function confirmMaster(edits = masterEdits(), version = master?.master_version) {
    setError("");
    return protect("confirm-master", { edits, version }, async (token) => {
      setConfirmingMaster(true);
      try {
        const result = await confirmMasterEdits(edits, version, token);
        setNotice(result.message || "Historical dataset updated successfully.");
        setMasterHighlight(result.revision_id || "");
        await refreshMaster(1, "changes", result.revision_id);
        await load();
      } finally {
        setConfirmingMaster(false);
      }
    }, { destructive: true });
  }

  function startMasterEdit() {
    setError("");
    return protect("edit-master", {}, async (token) => {
      await assertEditorAccess(token);
      if (!master) {
        await refreshMaster(1, masterFilter, masterHighlight, { keepEditing: true });
      }
      setMasterEditing(true);
    });
  }

  function openRollback(record) {
    setError("");
    return protect("open-rollback", { record: { id: record?.id, file_name: record?.file_name, rollback_allowed: true } }, async (token) => {
      await assertEditorAccess(token);
      setRollbackTarget(record?.id ? record : null);
      setRollbackReason("");
      setRollbackReview(null);
    });
  }

  function reviewRollback(next = {}) {
    setError("");
    const record = next.record || rollbackTarget;
    const id = next.id || record?.id;
    const reason = next.reason ?? rollbackReason;
    return protect("review-rollback", { id, reason, record }, async (token) => {
      setReviewingRollback(true);
      try {
        const payload = await previewRollback(id, reason, token);
        setRollbackTarget(record);
        setRollbackReason(reason);
        setRollbackReview(payload);
      } finally {
        setReviewingRollback(false);
      }
    }, { destructive: true });
  }

  function askRetrain() {
    setError("");
    return protect("ask-retrain", {}, async (token) => {
      await assertEditorAccess(token);
      setRetrainConfirm(true);
    });
  }

  function beginRetrain() {
    setError("");
    return protect("begin-retrain", {}, async (token) => {
      setRetrainStarting(true);
      try {
        await startRetrain(token);
        setRetrainConfirm(false);
        window.dispatchEvent(new Event("retrain-session"));
      } finally {
        setRetrainStarting(false);
      }
    }, { destructive: true });
  }

  function commitRollback(next = {}) {
    setError("");
    const record = next.record || rollbackTarget;
    const id = next.id || record?.id;
    const reason = next.reason ?? rollbackReason;
    const version = next.version ?? rollbackReview?.master_version;
    return protect("commit-rollback", { id, reason, version, record }, async (token) => {
      const result = await confirmRollback(id, reason, version, token);
      setNotice(result.message || "Rollback completed.");
      setRollbackTarget(null);
      setRollbackReason("");
      setRollbackReview(null);
      await load();
    }, { destructive: true });
  }

  function restoreActionConfirmation(resume) {
    const payload = resume.payload || {};
    if (resume.kind === "begin-retrain" || resume.kind === "ask-retrain") {
      setRetrainConfirm(true);
    }
    if (resume.kind === "open-rollback" || resume.kind === "review-rollback" || resume.kind === "commit-rollback") {
      setRollbackTarget(payload.record || null);
      setRollbackReason(payload.reason || "");
      setRollbackReview(null);
    }
  }

  function resumeAction(resume) {
    const payload = resume.payload || {};
    if (resume.kind === "choose-file") {
      return chooseFile();
    }
    if (resume.kind === "preview-dataset") {
      claimReturnedActionToken();
      takeReturnedActionExpiry();
      return Promise.resolve(false);
    }
    if (resume.kind === "edit-master") {
      return startMasterEdit();
    }
    if (resume.kind === "open-rollback") {
      return openRollback(payload.record || {});
    }
    if (resume.kind === "ask-retrain") {
      return askRetrain();
    }
    if (resume.kind === "begin-retrain") {
      return beginRetrain();
    }
    if (resume.kind === "confirm-dataset") {
      return handleSave(payload.previewId);
    }
    if (resume.kind === "stage-preview") {
      return stagePreview(payload.body, payload.previewId);
    }
    if (resume.kind === "preview-master") {
      return previewMaster(payload.edits || [], payload.version);
    }
    if (resume.kind === "confirm-master") {
      return confirmMaster(payload.edits || [], payload.version);
    }
    if (resume.kind === "review-rollback") {
      return reviewRollback(payload);
    }
    if (resume.kind === "commit-rollback") {
      return commitRollback(payload);
    }
    claimReturnedActionToken();
    return Promise.resolve(false);
  }

  const pageCount = Math.max(1, Math.ceil(total / 10));
  const showSummaryValues = !loading && !loadFailed && summary && !summary.empty;
  const showSummaryEmpty = !loading && !loadFailed && summary?.empty;

  return (
    <section className="settings-page">
      <header className="page-header">
        <div>
          <h1>Settings</h1>
          <p>Manage historical financial data.</p>
        </div>
      </header>

      {loadFailed ? (
        <OperationalAlert message={error || "Historical data could not be loaded."} onRetry={load} />
      ) : (
        <OperationalAlert message={error} />
      )}
      {notice ? (
        <p className="settings-success" role="status">
          {notice}
        </p>
      ) : null}

      <article className="card section-card">
        <h2>Current Data Summary</h2>
        {loading ? <p className="settings-loading">Loading historical data…</p> : null}
        {showSummaryEmpty ? <p className="muted">No historical actuals are stored yet. Upload a dataset to get started.</p> : null}
        {showSummaryValues ? (
          <div className="summary-grid settings-summary-grid">
            <SummaryCard
              label="History Period"
              value={formatHistoryPeriod(summary.earliest_month, summary.latest_month)}
            />
            <SummaryCard label="Categories" value={summary.category_count} />
            <SummaryCard label="Budget Codes" value={summary.budget_code_count} />
            <SummaryCard
              label={summary.data_source === "production_workbook" ? "Data Source" : "Last Upload"}
              value={
                summary.data_source === "production_workbook"
                  ? summary.data_source_label
                  : formatStamp(summary.last_upload_at)
              }
            />
          </div>
        ) : null}
        {loadFailed && !loading ? (
          <p className="muted">Summary values are hidden until historical data can be retrieved.</p>
        ) : null}
      </article>

      <article className="card section-card">
        <h2>Upload Dataset</h2>
        <div className="settings-upload-row">
          <input
            ref={fileInputRef}
            id="dataset-file"
            className="sr-only"
            type="file"
            accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
            onChange={(event) => {
              const next = event.target.files?.[0];
              if (!next) {
                return;
              }
              setFile(next);
              setPreview(null);
              setSheetName("");
            }}
          />
          <button
            type="button"
            className="settings-upload-button"
            onClick={chooseFile}
            disabled={!fileAuth.chooseEnabled || fileAuth.pending || actionPending}
          >
            Choose File
          </button>
          <button
            type="button"
            className="settings-upload-button"
            onClick={selectAuthorizedFile}
            disabled={!fileAuth.selectEnabled || fileAuth.pending || actionPending}
          >
            Select File
          </button>
          <span className="settings-file-name">{file?.name || "No file selected"}</span>
          <button type="button" className="generate-button settings-upload-validate" onClick={() => handlePreview()} disabled={validateDisabled || actionPending}>
            {previewing ? "Validating..." : "Validate & Preview"}
          </button>
        </div>
        {fileAuth.message ? (
          <p className={fileAuth.selectEnabled ? "settings-file-auth" : "settings-file-auth is-denied"} role="status">
            {fileAuth.message}
          </p>
        ) : null}
        <p className="muted settings-file-hint">Accepted formats: .xlsx, .csv</p>
        {preview?.needs_sheet_selection ? (
          <div className="field">
            <label htmlFor="dataset-sheet">Sheet</label>
            <select
              id="dataset-sheet"
              value={sheetName}
              onChange={(event) => {
                setSheetName(event.target.value);
                handlePreview(event.target.value);
              }}
            >
              <option value="">Select a sheet</option>
              {(preview.sheets || []).map((item) => (
                <option key={item.sheet} value={item.sheet}>
                  {item.sheet}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {preview ? (
          <PreviewWorkspace
            preview={preview}
            onStage={stagePreview}
            onConfirm={handleSave}
            onAuthorize={() => protect("remove-stage", {}, (token) => assertEditorAccess(token))}
            saving={saving}
            staging={staging}
            busy={actionPending}
          />
        ) : null}
      </article>

      <article className="card section-card">
        <h2>Master Dataset</h2>
        <p className="muted">The current historical master. Choosing a file does not change it.</p>
        <MasterSection
          master={master}
          editing={masterEditing}
          draft={masterDraft}
          preview={masterPreview}
          confirming={confirmingMaster}
          busy={actionPending}
          activeFilter={masterFilter}
          onView={() => refreshMaster(master?.page || 1, masterFilter, masterHighlight).catch((cause) => setError(cause.message))}
          onEdit={startMasterEdit}
          onDraft={draftMasterCell}
          onPreview={previewMaster}
          onConfirm={confirmMaster}
          onFilter={(filter) => {
            setMasterFilter(filter);
            refreshMaster(1, filter, masterHighlight).catch((cause) => setError(cause.message));
          }}
          onPage={(page) => refreshMaster(page, masterFilter, masterHighlight).catch((cause) => setError(cause.message))}
        />
      </article>

      <article className="card section-card retrain-card">
        <h2>Retrain Models</h2>
        <p>
          {modelStatus?.retraining_required
            ? "Data updated — retraining required"
            : modelStatus?.message || "Model publication metadata is unavailable."}
        </p>
        <p className="muted">
          {modelStatus?.published_at
            ? `Last successful publication: ${formatStamp(modelStatus.published_at)}`
            : "Last successful model publication time is unavailable."}
        </p>
        <button type="button" className="generate-button" onClick={askRetrain} disabled={actionPending}>
          Retrain Models
        </button>
      </article>

      <article className="card section-card">
        <h2>Revision History</h2>
        {loadFailed ? (
          <p className="muted">Saved files cannot be loaded right now.</p>
        ) : loading ? (
          <p className="settings-loading">Loading saved files…</p>
        ) : files.length === 0 ? (
          <p className="muted">No saved dataset files yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Uploaded By</th>
                  <th>Uploaded At</th>
                  <th>Last Edited By</th>
                  <th>Last Edited At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {files.map((record) => (
                  <tr key={record.id}>
                    <td>{record.file_name}</td>
                    <td>{record.uploaded_by}</td>
                    <td>{formatStamp(record.uploaded_at)}</td>
                    <td>{record.last_edited_by || "—"}</td>
                    <td>{formatStamp(record.last_edited_at)}</td>
                    <td className="settings-actions">
                      <button type="button" className="export-button-secondary" onClick={() => openView(record)}>
                        View
                      </button>
                      {record.rollback_allowed ? (
                        <button
                          type="button"
                          className="export-button-secondary"
                          onClick={() => openRollback(record)}
                          disabled={actionPending}
                        >
                          Rollback
                        </button>
                      ) : null}
                      {record.change_kind !== "MANUAL_EDIT" ? (
                        <button
                          type="button"
                          className="export-button-secondary"
                          onClick={() =>
                            downloadDatasetFile(record.id, record.file_name).catch((cause) => setError(cause.message))
                          }
                        >
                          Download
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!loadFailed && !loading && pageCount > 1 ? (
          <div className="settings-pagination">
            <button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
              Previous
            </button>
            <span>
              Page {page} of {pageCount}
            </span>
            <button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>
              Next
            </button>
          </div>
        ) : null}
      </article>

      {viewer ? <RevisionDetail detail={viewer} onClose={() => setViewer(null)} /> : null}
      {rollbackTarget ? (
        <RollbackDialog
          revision={rollbackTarget}
          reason={rollbackReason}
          review={rollbackReview}
          reviewing={reviewingRollback || actionPending}
          onReason={setRollbackReason}
          onReview={reviewRollback}
          onConfirm={commitRollback}
          onCancel={() => {
            setRollbackTarget(null);
            setRollbackReview(null);
          }}
        />
      ) : null}
      {retrainConfirm ? (
        <div className="settings-dialog-backdrop" role="presentation">
          <section className="card settings-dialog" role="dialog" aria-modal="true" aria-labelledby="retrain-confirm-title">
            <h2 id="retrain-confirm-title">Retrain Models</h2>
            <p>{CONFIRM_TEXT}</p>
            <div className="retrain-actions">
              <button type="button" className="generate-button" disabled={retrainStarting} onClick={beginRetrain}>
                {retrainStarting ? "Starting..." : "Continue"}
              </button>
              <button type="button" className="generate-button secondary" disabled={retrainStarting} onClick={() => setRetrainConfirm(false)}>
                Cancel
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
