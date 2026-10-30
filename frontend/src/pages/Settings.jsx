import { useCallback, useEffect, useRef, useState } from "react";
import {
  confirmDataset,
  confirmMasterEdits,
  confirmRollback,
  downloadDatasetFile,
  getDatasetSummary,
  getMasterDataset,
  listRevisions,
  getPreviewConfirmation,
  previewDataset,
  previewMasterEdits,
  previewRollback,
  stageDatasetPreview,
  viewRevision,
  assertEditorAccess,
  listBookEntries,
  lookupBookEntry,
  createBookEntry,
  updateBookEntry,
  deleteBookEntry,
  downloadBookEntryWorkbook,
} from "../api/datasetApi";
import { claimReturnedActionToken, consumeSettingsActionResume, jsonSafeData, requestSettingsAction, subscribeActionPrompt, takeActionFailure, takeReturnedActionExpiry } from "../auth/actionAuthentication";
import { getModelStatus, startRetrain } from "../api/retrainApi";
import ActivityLogPanel from "../components/ActivityLogPanel";
import { MasterSection, PreviewWorkspace, RevisionDetail, RollbackDialog } from "../components/HistoricalDataPanels";
import SummaryCard from "../components/SummaryCard";
import { formatHistoryPeriod } from "../utils/historyPeriod";
import { latestAvailableDataParts } from "../utils/overviewDashboard";
import { CONFIRM_TEXT, RETRAIN_SAVE_NOTICE } from "../utils/retrainWorkflow";
import {
  FILE_AUTH_DENIED,
  FILE_AUTH_EXPIRED,
  beginFileAuthentication,
  fileAuthenticationFailed,
  fileAuthenticationSucceeded,
  fileSelectionClick,
  initialFileSelection,
  subscribeFileSelectionReset,
} from "../utils/fileSelectionAuth";
import { confirmDatasetPayload, isAuthorizationCancellation } from "../utils/settingsActions";
import { bumpOverviewHistoryEpoch } from "../utils/overviewHistoryEpoch";

const BOOK_ENTRY_REMOVAL_WARNING = "Removing this account will make it eligible for forecasting again, subject to model and historical data availability.";

const SETTINGS_TABS = [
  { id: "data-management", label: "Data Management" },
  { id: "revision-history", label: "Revision History" },
  { id: "activity-log", label: "Activity Log" },
];

function BookEntrySection({ protect, setError, setNotice, actionPending, actionsRef }) {
  const [entries, setEntries] = useState({ count: 0, accounts: [], categories: [] });
  const [search, setSearch] = useState("");
  const [nameSearch, setNameSearch] = useState("");
  const [category, setCategory] = useState("");
  const [form, setForm] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [lookupNote, setLookupNote] = useState("");

  const refresh = useCallback(async (nextSearch = "", nextCategory = "", nextName = "") => {
    const payload = await listBookEntries(nextSearch, nextCategory, nextName);
    setEntries(payload || { count: 0, accounts: [], categories: [] });
  }, []);

  useEffect(() => {
    refresh().catch((cause) => setError(cause.message || "Book Entry accounts could not be loaded."));
  }, [refresh, setError]);

  function bookEntryDraft(draft, mode) {
    return {
      mode,
      budget_code: draft?.budget_code || "",
      budget_name: draft?.budget_name || "",
      category: draft?.category || "",
      locked: Boolean(draft?.locked),
    };
  }

  function denyBookEntryChange(cause) {
    if (Number(cause?.status) !== 403) {
      return false;
    }
    clearFlowAuthorization(BOOK_ENTRY_FLOW);
    setError(cause.message || "Your Microsoft department and job title are not allowed to change Book Entry accounts.");
    return true;
  }

  function beginAddBookEntry(draft) {
    const payload = bookEntryDraft(draft, "add");
    setError("");
    return protect("add-book-entry", payload, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(BOOK_ENTRY_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      setForm(payload);
      setLookupNote("");
      setPendingDelete(null);
      setNotice("Sign-in succeeded. Choose Save to add this Book Entry account. Nothing was saved.");
    }, { destructive: true });
  }

  function beginEditBookEntry(draft) {
    const payload = bookEntryDraft(draft, "edit");
    setError("");
    return protect("edit-book-entry", payload, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(BOOK_ENTRY_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      let next = payload;
      try {
        const found = payload.budget_code ? await lookupBookEntry(payload.budget_code) : null;
        if (found?.found) {
          next = {
            ...payload,
            budget_name: found.budget_name,
            category: found.category,
            locked: true,
          };
        }
      } catch {
        next = payload;
      }
      setForm(next);
      setPendingDelete(null);
      setNotice("Sign-in succeeded. Choose Save to update this Book Entry account. Nothing was saved.");
    }, { destructive: true });
  }

  function beginDeleteBookEntry(draft) {
    const payload = bookEntryDraft(draft, "delete");
    if (!payload.budget_code) {
      return Promise.resolve(false);
    }
    setError("");
    return protect("delete-book-entry", payload, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(BOOK_ENTRY_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      setPendingDelete(payload);
      setNotice("Sign-in succeeded. Choose Remove to delete this Book Entry account. Nothing was deleted.");
    }, { destructive: true });
  }

  async function submitBookEntry(draft) {
    const body = draft || form;
    if (!body?.budget_code) {
      setError("Enter a Budget Code.");
      return false;
    }
    const plan = planSessionAction({ token: readFlowAuthorization(BOOK_ENTRY_FLOW, Date.now()) });
    if (plan.type !== "call") {
      return body.mode === "edit" ? beginEditBookEntry(body) : beginAddBookEntry(body);
    }
    try {
      if (body.mode === "edit") {
        await updateBookEntry(body.budget_code, {
          budget_name: body.budget_name,
          category: body.category,
        }, plan.token);
      } else {
        await createBookEntry({
          budget_code: body.budget_code,
          budget_name: body.budget_name,
          category: body.category,
        }, plan.token);
      }
      clearFlowAuthorization(BOOK_ENTRY_FLOW);
      setForm(null);
      setLookupNote("");
      setNotice("Book Entry account saved. It is excluded from forecasting and exports.");
      await refresh(search, category, nameSearch);
      return true;
    } catch (cause) {
      if (denyBookEntryChange(cause)) {
        return false;
      }
      if (isEditorAuthorizationError(cause)) {
        clearFlowAuthorization(BOOK_ENTRY_FLOW);
        return body.mode === "edit" ? beginEditBookEntry(body) : beginAddBookEntry(body);
      }
      setError(cause.message || "The Book Entry account was not saved.");
      return false;
    }
  }

  async function confirmDeleteBookEntry(draft) {
    const body = draft || pendingDelete;
    if (!body?.budget_code) {
      return false;
    }
    const plan = planSessionAction({ token: readFlowAuthorization(BOOK_ENTRY_FLOW, Date.now()) });
    if (plan.type !== "call") {
      return beginDeleteBookEntry(body);
    }
    try {
      await deleteBookEntry(body.budget_code, plan.token);
      clearFlowAuthorization(BOOK_ENTRY_FLOW);
      setPendingDelete(null);
      setNotice("Book Entry account removed. Historical actuals, master data, and model artifacts were kept.");
      await refresh(search, category, nameSearch);
      return true;
    } catch (cause) {
      if (denyBookEntryChange(cause)) {
        return false;
      }
      if (isEditorAuthorizationError(cause)) {
        clearFlowAuthorization(BOOK_ENTRY_FLOW);
        return beginDeleteBookEntry(body);
      }
      setError(cause.message || "The Book Entry account was not removed.");
      return false;
    }
  }

  function restoreBookEntry(resume) {
    const payload = resume?.payload || {};
    if (resume?.kind === "delete-book-entry") {
      setPendingDelete(bookEntryDraft(payload, "delete"));
      return;
    }
    setForm(bookEntryDraft(payload, resume?.kind === "edit-book-entry" ? "edit" : "add"));
  }

  actionsRef.current = {
    beginAdd: beginAddBookEntry,
    beginEdit: beginEditBookEntry,
    beginDelete: beginDeleteBookEntry,
    restore: restoreBookEntry,
  };

  async function applyLookup(code) {
    const text = String(code || "").trim();
    if (!text || form?.mode === "edit") {
      return;
    }
    try {
      const found = await lookupBookEntry(text);
      if (found.found) {
        setForm((current) => ({
          ...(current || {}),
          mode: "add",
          budget_code: found.budget_code,
          budget_name: found.budget_name,
          category: found.category,
          locked: true,
        }));
        setLookupNote("This Budget Code is in the master dataset. Its name and category come from that record.");
      } else {
        setForm((current) => ({ ...(current || {}), budget_code: text, locked: false }));
        setLookupNote("This Budget Code is not in the master dataset. Enter its name and category.");
      }
      if (found.already_registered) {
        setError("This Budget Code is already a Book Entry account.");
      }
    } catch (cause) {
      setError(cause.message || "The Budget Code could not be checked.");
    }
  }

  const categoryOptions = entries.categories || [];

  return (
    <article className="card section-card book-entry-card">
      <h2>Book Entry Account Management</h2>
      <p>{entries.count} Book Entry Accounts are excluded from forecasting and exports.</p>
      <div className="book-entry-actions">
        <button
          type="button"
          className="generate-button"
          disabled={actionPending}
          onClick={() => beginAddBookEntry()}
        >
          Add Book Entry
        </button>
        <button
          type="button"
          className="generate-button secondary"
          onClick={() => downloadBookEntryWorkbook().catch((cause) => setError(cause.message || "The workbook could not be downloaded."))}
        >
          Download workbook
        </button>
      </div>
      <div className="book-entry-filters">
        <input
          aria-label="Search by Budget Code"
          placeholder="Search by Budget Code"
          value={search}
          onChange={(event) => {
            const value = event.target.value;
            setSearch(value);
            refresh(value, category, nameSearch).catch((cause) => setError(cause.message));
          }}
        />
        <input
          aria-label="Search by Budget Code Name"
          placeholder="Search by Budget Code Name"
          value={nameSearch}
          onChange={(event) => {
            const value = event.target.value;
            setNameSearch(value);
            refresh(search, category, value).catch((cause) => setError(cause.message));
          }}
        />
        <select
          aria-label="Filter by Category"
          value={category}
          onChange={(event) => {
            const value = event.target.value;
            setCategory(value);
            refresh(search, value, nameSearch).catch((cause) => setError(cause.message));
          }}
        >
          <option value="">All categories</option>
          {categoryOptions.map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
        <button
          type="button"
          className="generate-button secondary"
          onClick={() => {
            setSearch("");
            setNameSearch("");
            setCategory("");
            setForm((current) => (current?.mode ? current : null));
            refresh("", "", "").catch((cause) => setError(cause.message));
          }}
        >
          View all
        </button>
      </div>
      {form?.mode ? (
        <form
          className="book-entry-form"
          onSubmit={(event) => {
            event.preventDefault();
            submitBookEntry(form);
          }}
        >
          <label>
            Category
            <select
              value={form.category}
              disabled={form.locked}
              onChange={(event) => setForm({ ...form, category: event.target.value })}
            >
              <option value="">Select a category</option>
              {categoryOptions.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Budget Code
            <input
              value={form.budget_code}
              readOnly={form.mode === "edit"}
              onChange={(event) => setForm({ ...form, budget_code: event.target.value, locked: false })}
              onBlur={(event) => applyLookup(event.target.value)}
            />
          </label>
          <label>
            Budget Code Name
            <input
              value={form.budget_name}
              readOnly={form.locked}
              onChange={(event) => setForm({ ...form, budget_name: event.target.value })}
            />
          </label>
          {lookupNote ? <p className="muted">{lookupNote}</p> : null}
          {form.mode === "edit" ? <p className="muted">The Budget Code stays the same. Remove this account and add a new one to change it.</p> : null}
          <div className="book-entry-actions">
            <button type="submit" className="generate-button" disabled={actionPending}>Save</button>
            <button type="button" className="generate-button secondary" onClick={() => setForm(null)}>Cancel</button>
          </div>
        </form>
      ) : null}
      <div className="table-scroll">
        <table className="book-entry-table">
          <thead>
            <tr>
              <th>Category</th>
              <th>Budget Code</th>
              <th>Budget Code Name</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {(entries.accounts || []).map((account) => (
              <tr key={account.budget_code}>
                <td>{account.category}</td>
                <td>{account.budget_code}</td>
                <td>{account.budget_name}</td>
                <td>
                  <button
                    type="button"
                    className="generate-button secondary"
                    disabled={actionPending}
                    onClick={() => beginEditBookEntry(account)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="generate-button secondary"
                    disabled={actionPending}
                    onClick={() => beginDeleteBookEntry(account)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pendingDelete ? (
        <div className="settings-dialog-backdrop">
          <div className="settings-dialog" role="dialog" aria-modal="true">
            <h3>Remove Book Entry account {pendingDelete.budget_code}</h3>
            <p>{BOOK_ENTRY_REMOVAL_WARNING}</p>
            <div className="book-entry-actions">
              <button type="button" className="generate-button" disabled={actionPending} onClick={() => confirmDeleteBookEntry(pendingDelete)}>
                Remove
              </button>
              <button type="button" className="generate-button secondary" onClick={() => setPendingDelete(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}
import {
  BOOK_ENTRY_FLOW,
  MASTER_EDIT_FLOW,
  RETRAIN_FLOW,
  ROLLBACK_FLOW,
  UPLOAD_FLOW,
  clearFlowAuthorization,
  grantFlowAuthorization,
  readFlowAuthorization,
} from "../utils/flowAuthorization";
import {
  SAVE_REFRESH_FAILURE_MESSAGE,
  SAVE_UNKNOWN_MESSAGE,
  claimSaveLock,
  clearMasterReview,
  clearUploadReview,
  commitReviewedUpload,
  isEditorAuthorizationError,
  isUncertainSaveError,
  markPreviewCompleted,
  outcomeFromConfirmationStatus,
  planSessionAction,
  planUploadAction,
  readCompletedPreview,
  readMasterReview,
  restoreUploadReview,
  saveMasterReview,
  saveUploadReview,
  settleSaveOutcome,
} from "../utils/settingsFlow";

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
  const [savePhase, setSavePhase] = useState("idle");
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
  const [activeTab, setActiveTab] = useState("data-management");
  const [fileAuth, setFileAuth] = useState(initialFileSelection);
  const fileAuthRef = useRef(fileAuth);
  const previewRef = useRef(null);
  const masterRef = useRef(null);
  const masterDraftRef = useRef({});
  const masterPreviewRef = useRef(null);
  const saveLock = useRef(false);
  const retrainButtonRef = useRef(null);
  const retrainDialogRef = useRef(null);
  const startTrainingRef = useRef(null);
  const retrainStartLock = useRef(false);
  const bookEntryActions = useRef({});

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
        clearFlowAuthorization(UPLOAD_FLOW);
        updateFileAuth(fileAuthenticationFailed(failure.message));
        setError(failure.message);
      } else if (failure?.message) {
        setError(failure.message);
      }
    }
  }), []);

  useEffect(() => subscribeFileSelectionReset(() => {
    updateFileAuth(initialFileSelection());
    setFile(null);
    clearFlowAuthorization(UPLOAD_FLOW);
    previewRef.current = null;
    setPreview(null);
    clearUploadReview(sessionStorage);
  }), []);

  useEffect(() => {
    const restored = restoreUploadReview(sessionStorage);
    if (restored?.type === "review" && restored.preview) {
      previewRef.current = restored.preview;
      setPreview(restored.preview);
    }
    const masterSaved = readMasterReview(sessionStorage);
    if (masterSaved?.editing) {
      masterDraftRef.current = masterSaved.draft || {};
      setMasterDraft(masterSaved.draft || {});
      masterPreviewRef.current = masterSaved.preview || null;
      setMasterPreview(masterSaved.preview || null);
      setMasterEditing(true);
    }
  }, []);

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
      "renew-upload",
      "renew-master-edit",
      "renew-rollback",
      "renew-retrain-continue",
      "confirm-dataset",
      "stage-preview",
      "preview-master",
      "confirm-master",
      "review-rollback",
      "commit-rollback",
      "preview-dataset",
      "remove-stage",
      "add-book-entry",
      "edit-book-entry",
      "delete-book-entry",
      "view-revision",
      "download-revision",
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

  useEffect(() => {
    if (!retrainConfirm) {
      return undefined;
    }
    const dialog = retrainDialogRef.current;
    const previouslyFocused = retrainButtonRef.current;
    startTrainingRef.current?.focus();
    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!retrainStartLock.current) {
          cancelRetrainConfirm();
        }
        return;
      }
      if (event.key !== "Tab" || !dialog) {
        return;
      }
      const items = [...dialog.querySelectorAll("button:not(:disabled)")];
      if (!items.length) {
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused?.focus();
    };
  }, [retrainConfirm]);

  function replacePreview(next, options = {}) {
    previewRef.current = next;
    setPreview(next);
    if (options.persist === false) {
      return;
    }
    if (next?.preview_id) {
      try {
        saveUploadReview(next, sessionStorage);
      } catch {
        // The on-screen review stays available when the snapshot cannot be stored.
      }
      return;
    }
    clearUploadReview(sessionStorage);
  }

  function rememberMasterSession() {
    try {
      saveMasterReview({
        draft: masterDraftRef.current,
        preview: masterPreviewRef.current,
        version: masterRef.current?.master_version ?? null,
      }, sessionStorage);
    } catch {
      // Keep the in-memory edits when the snapshot cannot be stored.
    }
  }

  function restoreMasterSession() {
    const saved = readMasterReview(sessionStorage);
    if (!saved) {
      return;
    }
    masterDraftRef.current = saved.draft || {};
    setMasterDraft(saved.draft || {});
    masterPreviewRef.current = saved.preview || null;
    setMasterPreview(saved.preview || null);
    setMasterEditing(true);
  }

  function renewUpload(previewId) {
    if (previewRef.current?.preview_id) {
      try {
        saveUploadReview(previewRef.current, sessionStorage);
      } catch {
        // The current review remains on screen until the redirect.
      }
    }
    setNotice("");
    setError("Authentication expired or was rejected. Sign in again, then choose Confirm & Save. The review was kept and the historical master was not changed.");
    return protect("renew-upload", { previewId: String(previewId || previewRef.current?.preview_id || "") }, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(UPLOAD_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      const fileGrant = fileAuthenticationSucceeded(expiresAt, Date.now());
      if (fileGrant.selectEnabled) {
        updateFileAuth(fileGrant);
      }
      const restored = restoreUploadReview(sessionStorage);
      if (restored?.type === "review") {
        replacePreview(restored.preview, { persist: false });
      }
      setError("");
      setNotice("Authentication was renewed. Review the changes, then choose Confirm & Save. The historical master was not changed.");
    });
  }

  function handlePreview(nextSheet) {
    if (!file) {
      return Promise.resolve(false);
    }
    setError("");
    setNotice("");
    const plan = planUploadAction({
      token: readFlowAuthorization(UPLOAD_FLOW, Date.now()),
      preview: previewRef.current,
      action: "preview",
      completedPreviewId: readCompletedPreview(sessionStorage),
    });
    if (plan.type === "reauthenticate-entry") {
      clearFlowAuthorization(UPLOAD_FLOW);
      updateFileAuth(fileAuthenticationFailed(FILE_AUTH_EXPIRED));
      setFile(null);
      setError(FILE_AUTH_EXPIRED);
      return Promise.resolve(false);
    }
    if (plan.type !== "call") {
      return renewUpload(plan.previewId);
    }
    setPreviewing(true);
    return previewDataset(file, nextSheet || sheetName || undefined, plan.token)
      .then((payload) => {
        replacePreview(payload);
        if (payload.selected_sheet) {
          setSheetName(payload.selected_sheet);
        }
        return true;
      })
      .catch((cause) => {
        if (isEditorAuthorizationError(cause)) {
          clearFlowAuthorization(UPLOAD_FLOW);
          setError("Authentication was rejected. Sign in again. The historical master was not changed.");
          if (previewRef.current?.preview_id) {
            return renewUpload(previewRef.current.preview_id);
          }
          updateFileAuth(fileAuthenticationFailed(cause.message || FILE_AUTH_DENIED));
          return false;
        }
        if (!isAuthorizationCancellation(cause)) {
          replacePreview(null);
          setError(cause.message || "The file could not be validated.");
        }
        return false;
      })
      .finally(() => setPreviewing(false));
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
      if (!granted.selectEnabled || !grantFlowAuthorization(UPLOAD_FLOW, token, expiresAt)) {
        clearFlowAuthorization(UPLOAD_FLOW);
        throw new Error(granted.message || FILE_AUTH_EXPIRED);
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

  async function handleSave(review) {
    const previewId = typeof review === "string" ? review : (previewRef.current?.preview_id || "");
    let descriptor;
    try {
      descriptor = confirmDatasetPayload(previewId);
    } catch (cause) {
      claimReturnedActionToken();
      setError(cause.message || "Confirm & Save requires a preview id.");
      return false;
    }
    const completed = readCompletedPreview(sessionStorage);
    const plan = planUploadAction({
      token: readFlowAuthorization(UPLOAD_FLOW, Date.now()),
      preview: { preview_id: descriptor.previewId },
      action: "confirm",
      completedPreviewId: completed,
    });
    if (savePhase === "saving" || savePhase === "saved") {
      return false;
    }
    if (plan.type === "already-saved") {
      setSavePhase("saved");
      setError("");
      setNotice("Changes saved to the historical master successfully.");
      return false;
    }
    if (plan.type !== "call") {
      return renewUpload(descriptor.previewId);
    }
    if (!claimSaveLock(saveLock)) {
      return false;
    }
    let reviewBody = null;
    if (review && typeof review === "object") {
      try {
        reviewBody = jsonSafeData(review);
      } catch (cause) {
        saveLock.current = false;
        setError(cause.message || "Confirm & Save could not read the review.");
        return false;
      }
    }
    setSaving(true);
    setSavePhase("saving");
    setError("");
    setNotice("");
    const signal = typeof AbortSignal !== "undefined" && AbortSignal.timeout ? AbortSignal.timeout(120000) : undefined;
    let commitResult = null;
    try {
      const outcome = await commitReviewedUpload({
        previewId: descriptor.previewId,
        reviewBody,
        token: plan.token,
        completedPreviewId: completed,
        stage: stageDatasetPreview,
        confirm: confirmDataset,
        signal,
      });
      if (!outcome.saved) {
        if (outcome.preview) {
          replacePreview(outcome.preview);
        }
        commitResult = {
          saved: false,
          reasonText: outcome.reason === "unresolved"
            ? "Confirm & Save stays disabled until the decisions below are resolved."
            : "The historical master was not updated.",
        };
      } else {
        commitResult = { saved: true, result: outcome.result || {}, previewId: outcome.previewId };
      }
    } catch (cause) {
      if (isEditorAuthorizationError(cause)) {
        clearFlowAuthorization(UPLOAD_FLOW);
        setSavePhase("failed");
        setError("Changes were not saved. Sign in again, then choose Confirm & Save.");
        return renewUpload(descriptor.previewId);
      }
      const settled = settleSaveOutcome({ requestError: cause });
      setSavePhase(settled.phase);
      setError(settled.error);
      setNotice(settled.notice);
      if (isUncertainSaveError(cause)) {
        return false;
      }
      return false;
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
    if (!commitResult?.saved) {
      const settled = settleSaveOutcome({ commitResult });
      setSavePhase(settled.phase);
      setError(settled.error);
      setNotice("");
      return false;
    }
    markPreviewCompleted(commitResult.previewId, sessionStorage);
    setSavePhase("saved");
    setError("");
    setNotice("Changes saved to the historical master successfully.");
    setMasterHighlight(commitResult.result.file_id || "");
    setMasterFilter("changes");
    bumpOverviewHistoryEpoch(sessionStorage);
    try {
      await load();
      await refreshMaster(1, "changes", commitResult.result.file_id);
      return true;
    } catch {
      setSavePhase("saved");
      setError("");
      setNotice(SAVE_REFRESH_FAILURE_MESSAGE);
      return true;
    }
  }

  async function checkSaveStatus() {
    const previewId = previewRef.current?.preview_id || readCompletedPreview(sessionStorage);
    if (!previewId || savePhase === "saving") {
      return false;
    }
    const token = readFlowAuthorization(UPLOAD_FLOW, Date.now());
    if (!token) {
      setError(SAVE_UNKNOWN_MESSAGE);
      return renewUpload(previewId);
    }
    try {
      const payload = await getPreviewConfirmation(previewId, token);
      const settled = outcomeFromConfirmationStatus(payload);
      setSavePhase(settled.phase);
      setError(settled.error);
      setNotice(settled.notice);
      if (settled.phase !== "saved") {
        return false;
      }
      markPreviewCompleted(previewId, sessionStorage);
      setMasterHighlight(settled.result?.file_id || "");
      setMasterFilter("changes");
      bumpOverviewHistoryEpoch(sessionStorage);
      try {
        await load();
        await refreshMaster(1, "changes", settled.result?.file_id);
      } catch {
        setSavePhase("saved");
        setError("");
        setNotice(SAVE_REFRESH_FAILURE_MESSAGE);
      }
      return true;
    } catch (cause) {
      if (isUncertainSaveError(cause) || !isEditorAuthorizationError(cause)) {
        setSavePhase("unknown");
        setNotice("");
        setError(SAVE_UNKNOWN_MESSAGE);
        return false;
      }
      clearFlowAuthorization(UPLOAD_FLOW);
      setError(SAVE_UNKNOWN_MESSAGE);
      return renewUpload(previewId);
    }
  }

  function openView(record) {
    setError("");
    return protect("view-revision", { revisionId: record.id }, async (token) => {
      try {
        const payload = await viewRevision(record.id, token);
        setViewer(payload);
        return true;
      } catch (cause) {
        setError(cause.message || "Unable to open the saved file.");
        return false;
      }
    });
  }

  function downloadRevision(record) {
    setError("");
    return protect(
      "download-revision",
      { revisionId: record.id, fileName: record.file_name || "" },
      async (token) => {
        try {
          await downloadDatasetFile(record.id, record.file_name, token);
          return true;
        } catch (cause) {
          setError(cause.message || "Unable to download the saved file.");
          return false;
        }
      },
    );
  }

  async function refreshMaster(page = 1, filter = masterFilter, highlight = masterHighlight, options = {}) {
    const payload = await getMasterDataset({ page, pageSize: 20, revisionFilter: filter, highlight });
    masterRef.current = payload;
    setMaster(payload);
    if (!options.keepEditing) {
      setMasterEditing(false);
      masterDraftRef.current = {};
      setMasterDraft({});
      masterPreviewRef.current = null;
      setMasterPreview(null);
      clearMasterReview(sessionStorage);
    }
  }

  function stagePreview(body, previewId = previewRef.current?.preview_id) {
    setError("");
    const plan = planUploadAction({
      token: readFlowAuthorization(UPLOAD_FLOW, Date.now()),
      preview: { preview_id: previewId || "" },
      action: "stage",
      completedPreviewId: readCompletedPreview(sessionStorage),
    });
    if (plan.type !== "call") {
      return renewUpload(previewId);
    }
    let reviewBody;
    try {
      reviewBody = jsonSafeData(body);
    } catch (cause) {
      setError(cause.message || "The preview edits could not be saved.");
      return Promise.resolve(false);
    }
    setStaging(true);
    return stageDatasetPreview(plan.previewId, reviewBody, plan.token)
      .then((payload) => {
        replacePreview(payload);
        return true;
      })
      .catch((cause) => {
        if (isEditorAuthorizationError(cause)) {
          clearFlowAuthorization(UPLOAD_FLOW);
          setError("Authentication was rejected. Sign in again. The historical master was not changed.");
          return renewUpload(previewId);
        }
        setError(cause.message || "The preview edits could not be saved.");
        return false;
      })
      .finally(() => setStaging(false));
  }

  function draftMasterCell(row, month, value) {
    const next = { ...masterDraftRef.current, [`${row.budget_code}|${month}`]: value };
    masterDraftRef.current = next;
    setMasterDraft(next);
    rememberMasterSession();
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

  function renewMasterEdit() {
    rememberMasterSession();
    setError("Authentication expired or was rejected. Sign in again, then choose Save Changes or Confirm Changes. Edits were kept and the historical master was not changed.");
    return protect("renew-master-edit", { version: masterRef.current?.master_version ?? null }, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(MASTER_EDIT_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      restoreMasterSession();
      setError("");
      setNotice("Authentication was renewed. Review the edits, then choose Confirm Changes. The historical master was not changed.");
    });
  }

  function previewMaster(edits = masterEdits(), version = masterRef.current?.master_version) {
    setError("");
    const plan = planSessionAction({ token: readFlowAuthorization(MASTER_EDIT_FLOW, Date.now()) });
    if (plan.type !== "call") {
      return renewMasterEdit();
    }
    return previewMasterEdits(edits, version, plan.token)
      .then((payload) => {
        masterPreviewRef.current = payload;
        setMasterPreview(payload);
        return true;
      })
      .catch((cause) => {
        if (isEditorAuthorizationError(cause)) {
          clearFlowAuthorization(MASTER_EDIT_FLOW);
          return renewMasterEdit();
        }
        setError(cause.message || "The edits could not be reviewed.");
        return false;
      });
  }

  async function confirmMaster(edits = masterEdits(), version = masterRef.current?.master_version) {
    setError("");
    const plan = planSessionAction({ token: readFlowAuthorization(MASTER_EDIT_FLOW, Date.now()) });
    if (plan.type !== "call") {
      return renewMasterEdit();
    }
    setConfirmingMaster(true);
    try {
      const result = await confirmMasterEdits(edits, version, plan.token);
      clearFlowAuthorization(MASTER_EDIT_FLOW);
      clearMasterReview(sessionStorage);
      masterDraftRef.current = {};
      masterPreviewRef.current = null;
      setNotice(result.message || "Historical dataset updated successfully.");
      setMasterHighlight(result.revision_id || "");
      bumpOverviewHistoryEpoch(sessionStorage);
      await refreshMaster(1, "changes", result.revision_id);
      await load();
      return true;
    } catch (cause) {
      if (isEditorAuthorizationError(cause)) {
        clearFlowAuthorization(MASTER_EDIT_FLOW);
        return renewMasterEdit();
      }
      setError(cause.message || "The historical master was not changed.");
      return false;
    } finally {
      setConfirmingMaster(false);
    }
  }

  function startMasterEdit() {
    setError("");
    return protect("edit-master", {}, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(MASTER_EDIT_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      if (!masterRef.current) {
        await refreshMaster(1, masterFilter, masterHighlight, { keepEditing: true });
      }
      setMasterEditing(true);
      restoreMasterSession();
    });
  }

  function rollbackRecord(record) {
    return {
      id: record?.id || "",
      file_name: record?.file_name || "",
      rollback_allowed: Boolean(record?.rollback_allowed),
    };
  }

  function renewRollback(record) {
    const safe = rollbackRecord(record);
    setError("Authentication expired or was rejected. Sign in again, then review the rollback. Nothing was rolled back.");
    return protect("renew-rollback", { record: safe }, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(ROLLBACK_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      setRollbackTarget(safe.id ? safe : null);
      setError("");
      setNotice("Authentication was renewed. Review the rollback, then confirm it. Nothing was rolled back.");
    });
  }

  function openRollback(record) {
    setError("");
    const safe = rollbackRecord(record);
    return protect("open-rollback", { record: safe }, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(ROLLBACK_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      setRollbackTarget(safe.id ? safe : null);
      setRollbackReason("");
      setRollbackReview(null);
    });
  }

  function reviewRollback(next = {}) {
    setError("");
    const record = next.record || rollbackTarget;
    const id = next.id || record?.id;
    const reason = next.reason ?? rollbackReason;
    const plan = planSessionAction({ token: readFlowAuthorization(ROLLBACK_FLOW, Date.now()) });
    if (plan.type !== "call") {
      return renewRollback(record);
    }
    setReviewingRollback(true);
    return previewRollback(id, reason, plan.token)
      .then((payload) => {
        setRollbackTarget(record);
        setRollbackReason(reason);
        setRollbackReview(payload);
        return true;
      })
      .catch((cause) => {
        if (isEditorAuthorizationError(cause)) {
          clearFlowAuthorization(ROLLBACK_FLOW);
          return renewRollback(record);
        }
        setError(cause.message || "The rollback could not be reviewed.");
        return false;
      })
      .finally(() => setReviewingRollback(false));
  }

  function renewRetrainContinue() {
    setRetrainConfirm(true);
    setError("Authentication expired or was rejected. Sign in again, then choose Start Training. Training was not started.");
    return protect("renew-retrain-continue", {}, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(RETRAIN_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      setRetrainConfirm(true);
      setError("");
      setNotice("Authentication was renewed. Choose Start Training to start training. Training was not started.");
    });
  }

  function askRetrain() {
    setError("");
    return protect("ask-retrain", {}, async (token, expiresAt) => {
      await assertEditorAccess(token);
      if (!grantFlowAuthorization(RETRAIN_FLOW, token, expiresAt)) {
        throw new Error(FILE_AUTH_EXPIRED);
      }
      setRetrainConfirm(true);
    });
  }

  function cancelRetrainConfirm() {
    if (retrainStarting || retrainStartLock.current) {
      return;
    }
    clearFlowAuthorization(RETRAIN_FLOW);
    setRetrainConfirm(false);
  }

  async function beginRetrain() {
    if (retrainStartLock.current) {
      return false;
    }
    retrainStartLock.current = true;
    setError("");
    const plan = planSessionAction({ token: readFlowAuthorization(RETRAIN_FLOW, Date.now()) });
    if (plan.type !== "call") {
      retrainStartLock.current = false;
      return renewRetrainContinue();
    }
    setRetrainStarting(true);
    try {
      await startRetrain(plan.token);
      setRetrainConfirm(false);
      window.dispatchEvent(new Event("retrain-session"));
      return true;
    } catch (cause) {
      if (isEditorAuthorizationError(cause)) {
        clearFlowAuthorization(RETRAIN_FLOW);
        return renewRetrainContinue();
      }
      setError(cause.message || "Retraining was not started.");
      return false;
    } finally {
      retrainStartLock.current = false;
      setRetrainStarting(false);
    }
  }

  async function commitRollback(next = {}) {
    setError("");
    const record = next.record || rollbackTarget;
    const id = next.id || record?.id;
    const reason = next.reason ?? rollbackReason;
    const version = next.version ?? rollbackReview?.master_version;
    const plan = planSessionAction({ token: readFlowAuthorization(ROLLBACK_FLOW, Date.now()) });
    if (plan.type !== "call") {
      return renewRollback(record);
    }
    try {
      const result = await confirmRollback(id, reason, version, plan.token);
      clearFlowAuthorization(ROLLBACK_FLOW);
      setNotice(result.message || "Rollback completed.");
      setRollbackTarget(null);
      setRollbackReason("");
      setRollbackReview(null);
      bumpOverviewHistoryEpoch(sessionStorage);
      await load();
      return true;
    } catch (cause) {
      if (isEditorAuthorizationError(cause)) {
        clearFlowAuthorization(ROLLBACK_FLOW);
        return renewRollback(record);
      }
      setError(cause.message || "The rollback was not completed.");
      return false;
    }
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
    if (resume.kind === "add-book-entry" || resume.kind === "edit-book-entry" || resume.kind === "delete-book-entry") {
      bookEntryActions.current.restore?.(resume);
    }
  }

  function resumeAction(resume) {
    const payload = resume.payload || {};
    if (resume.kind === "choose-file") {
      return chooseFile();
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
    if (
      resume.kind === "renew-upload"
      || resume.kind === "preview-dataset"
      || resume.kind === "confirm-dataset"
      || resume.kind === "stage-preview"
      || resume.kind === "remove-stage"
    ) {
      return renewUpload(payload.previewId);
    }
    if (resume.kind === "renew-master-edit" || resume.kind === "preview-master" || resume.kind === "confirm-master") {
      return renewMasterEdit();
    }
    if (resume.kind === "begin-retrain" || resume.kind === "renew-retrain-continue") {
      return renewRetrainContinue();
    }
    if (resume.kind === "renew-rollback" || resume.kind === "review-rollback" || resume.kind === "commit-rollback") {
      return renewRollback(payload.record || {});
    }
    if (resume.kind === "add-book-entry") {
      return bookEntryActions.current.beginAdd?.(payload);
    }
    if (resume.kind === "edit-book-entry") {
      return bookEntryActions.current.beginEdit?.(payload);
    }
    if (resume.kind === "delete-book-entry") {
      return bookEntryActions.current.beginDelete?.(payload);
    }
    if (resume.kind === "view-revision") {
      return openView({ id: payload.revisionId });
    }
    if (resume.kind === "download-revision") {
      return downloadRevision({ id: payload.revisionId, file_name: payload.fileName });
    }
    claimReturnedActionToken();
    return Promise.resolve(false);
  }

  const pageCount = Math.max(1, Math.ceil(total / 10));
  const showSummaryValues = !loading && !loadFailed && summary && !summary.empty;
  const latestAvailable = showSummaryValues ? latestAvailableDataParts(summary) : null;
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

      <div className="analytics-toolbar settings-toolbar">
        <div className="analytics-toolbar-tabs">
          <div className="analytics-tabs" role="tablist" aria-label="Settings views">
            {SETTINGS_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={`settings-tab-${tab.id}`}
                aria-selected={activeTab === tab.id}
                aria-controls={`settings-panel-${tab.id}`}
                className={activeTab === tab.id ? "active" : ""}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div
        className="analytics-tab-panel settings-tab-panel"
        role="tabpanel"
        id="settings-panel-data-management"
        aria-labelledby="settings-tab-data-management"
        hidden={activeTab !== "data-management"}
      >
      <article className="card section-card">
        <h2>Current Data Summary</h2>
        {loading ? <p className="settings-loading">Loading historical data…</p> : null}
        {showSummaryEmpty ? <p className="muted">No historical actuals are stored yet. Upload a dataset to get started.</p> : null}
        {showSummaryValues ? (
          <div className="summary-grid settings-summary-grid">
            <SummaryCard
              className="settings-summary-card settings-summary-card-period"
              label="History Period"
              value={formatHistoryPeriod(summary.earliest_month, summary.latest_month)}
            />
            <SummaryCard className="settings-summary-card" label="Categories" value={summary.category_count} />
            <SummaryCard className="settings-summary-card" label="Budget Codes" value={summary.budget_code_count} />
            <SummaryCard
              className="settings-summary-card"
              label="Latest complete year"
              value={summary.latest_complete_year ?? "Unavailable"}
            />
            <SummaryCard
              className="settings-summary-card settings-summary-card-latest"
              label="Latest available data"
              value={latestAvailable?.value}
              detail={latestAvailable?.detail}
            />
            <SummaryCard
              className="settings-summary-card"
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
              replacePreview(null);
              setSheetName("");
              setSavePhase("idle");
              setSaving(false);
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
            onAuthorize={() => {
              const token = readFlowAuthorization(UPLOAD_FLOW, Date.now());
              if (!token) {
                return renewUpload(previewRef.current?.preview_id).then(() => false);
              }
              return assertEditorAccess(token).then(() => true).catch((cause) => {
                if (isEditorAuthorizationError(cause)) {
                  clearFlowAuthorization(UPLOAD_FLOW);
                  return renewUpload(previewRef.current?.preview_id).then(() => false);
                }
                setError(cause.message || "The change was not authorized.");
                return false;
              });
            }}
            saving={saving}
            savePhase={savePhase}
            staging={staging}
            busy={actionPending}
            onCheckStatus={() => checkSaveStatus()}
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

      <BookEntrySection
        protect={protect}
        setError={setError}
        setNotice={setNotice}
        actionPending={actionPending}
        actionsRef={bookEntryActions}
      />

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
        <button ref={retrainButtonRef} type="button" className="generate-button" onClick={askRetrain} disabled={actionPending}>
          Retrain Models
        </button>
      </article>
      </div>

      <div
        className="analytics-tab-panel settings-tab-panel"
        role="tabpanel"
        id="settings-panel-revision-history"
        aria-labelledby="settings-tab-revision-history"
        hidden={activeTab !== "revision-history"}
      >
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
                          onClick={() => downloadRevision(record)}
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
      </div>

      <div
        className="analytics-tab-panel settings-tab-panel"
        role="tabpanel"
        id="settings-panel-activity-log"
        aria-labelledby="settings-tab-activity-log"
        hidden={activeTab !== "activity-log"}
      >
        <ActivityLogPanel active={activeTab === "activity-log"} />
      </div>

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
            clearFlowAuthorization(ROLLBACK_FLOW);
            setRollbackTarget(null);
            setRollbackReview(null);
          }}
        />
      ) : null}
      {retrainConfirm ? (
        <div className="retrain-confirm-backdrop">
          <section
            ref={retrainDialogRef}
            className="retrain-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="retrain-confirm-title"
            aria-describedby="retrain-confirm-copy retrain-confirm-notice"
          >
            <div className="retrain-confirm-heading">
              <span className="retrain-confirm-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M4 12a8 8 0 0 1 13.7-5.6L20 8" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M20 4v4h-4" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M20 12a8 8 0 0 1-13.7 5.6L4 16" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M4 20v-4h4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <h2 id="retrain-confirm-title">Retrain Models?</h2>
            </div>
            <p id="retrain-confirm-copy" className="retrain-confirm-copy">{CONFIRM_TEXT}</p>
            <p id="retrain-confirm-notice" className="retrain-confirm-notice">{RETRAIN_SAVE_NOTICE}</p>
            <div className="retrain-confirm-actions">
              <button type="button" className="retrain-confirm-cancel" disabled={retrainStarting} onClick={cancelRetrainConfirm}>
                Cancel
              </button>
              <button ref={startTrainingRef} type="button" className="retrain-confirm-start" disabled={retrainStarting} onClick={beginRetrain}>
                {retrainStarting ? "Starting..." : "Start Training"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
