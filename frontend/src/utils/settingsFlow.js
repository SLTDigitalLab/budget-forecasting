import { jsonSafeData } from "../auth/actionAuthentication.js";

export const UPLOAD_REVIEW_KEY = "settings_upload_review";
export const MASTER_REVIEW_KEY = "settings_master_edit_review";
export const COMPLETED_PREVIEW_KEY = "settings_completed_preview";

const SECRET_KEY = /token|authorization|secret/i;

export function isEditorAuthorizationError(error) {
  const status = Number(error?.status);
  return status === 401 || status === 403;
}

function omitSecrets(value) {
  if (Array.isArray(value)) {
    return value.map((item) => omitSecrets(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !SECRET_KEY.test(key))
        .map(([key, item]) => [key, omitSecrets(item)]),
    );
  }
  return value;
}

function writeReview(storage, key, value) {
  const record = omitSecrets(jsonSafeData(value));
  storage.setItem(key, JSON.stringify(record));
  return record;
}

export function saveUploadReview(preview, storage) {
  const previewId = String(preview?.preview_id || "").trim();
  if (!previewId) {
    throw new TypeError("Confirm & Save requires a preview id.");
  }
  return writeReview(storage, UPLOAD_REVIEW_KEY, {
    previewId,
    preview,
  });
}

export function readCompletedPreview(storage) {
  return storage.getItem(COMPLETED_PREVIEW_KEY) || "";
}

export function markPreviewCompleted(previewId, storage) {
  storage.setItem(COMPLETED_PREVIEW_KEY, String(previewId || ""));
  storage.removeItem(UPLOAD_REVIEW_KEY);
}

export function clearUploadReview(storage) {
  storage.removeItem(UPLOAD_REVIEW_KEY);
}

export function restoreUploadReview(storage) {
  const raw = storage.getItem(UPLOAD_REVIEW_KEY);
  if (!raw) {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  const completed = readCompletedPreview(storage);
  if (parsed?.previewId && parsed.previewId === completed) {
    storage.removeItem(UPLOAD_REVIEW_KEY);
    return { type: "completed", previewId: parsed.previewId };
  }
  if (!parsed?.previewId || !parsed?.preview) {
    return null;
  }
  return { type: "review", previewId: parsed.previewId, preview: parsed.preview };
}

export function saveMasterReview(review, storage) {
  return writeReview(storage, MASTER_REVIEW_KEY, {
    editing: true,
    draft: review?.draft || {},
    preview: review?.preview || null,
    version: review?.version ?? null,
  });
}

export function readMasterReview(storage) {
  const raw = storage.getItem(MASTER_REVIEW_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    return parsed?.editing ? parsed : null;
  } catch {
    return null;
  }
}

export function clearMasterReview(storage) {
  storage.removeItem(MASTER_REVIEW_KEY);
}

export function planUploadAction({ token, preview, action, completedPreviewId = "" }) {
  const previewId = String(preview?.preview_id || "");
  if (action === "confirm" && previewId && completedPreviewId === previewId) {
    return { type: "already-saved", previewId, submit: false };
  }
  if (token) {
    return { type: "call", token, previewId, submit: action === "confirm" };
  }
  if (previewId) {
    return { type: "renew", previewId, preserveReview: true, submit: false };
  }
  return { type: "reauthenticate-entry", submit: false };
}

export function planSessionAction({ token }) {
  if (token) {
    return { type: "call", token, submit: true };
  }
  return { type: "renew", submit: false };
}

export const SAVING_LABEL = "Saving…";
export const SAVED_LABEL = "Saved";
export const SAVE_SUCCESS_MESSAGE = "Changes saved to the historical master successfully.";
export const SAVE_FAILURE_MESSAGE = "Changes were not saved";
export const SAVE_UNKNOWN_MESSAGE = "Unable to confirm save status";
export const SAVE_REFRESH_FAILURE_MESSAGE = "Saved successfully, but refreshing the display failed.";

const UNSAFE_REASON = /traceback|stack trace|psycopg|sqlstate|password|secret|access token|bearer /i;

export function claimSaveLock(lock) {
  if (lock?.current) {
    return false;
  }
  lock.current = true;
  return true;
}

export function isUncertainSaveError(error) {
  if (!error || error.status) {
    return false;
  }
  const name = String(error.name || "");
  if (name === "AbortError" || name === "TimeoutError") {
    return true;
  }
  return /network|failed to fetch|timeout|aborted|disconnect/i.test(String(error.message || ""));
}

export function safeSaveReason(error) {
  const message = String(error?.message || "").replace(/\s+/g, " ").trim();
  if (!message || UNSAFE_REASON.test(message)) {
    return "The historical master could not be updated.";
  }
  return message.slice(0, 300);
}

export function interpretConfirmationStatus(payload) {
  if (payload?.status === "committed") {
    return { phase: "saved", result: payload.result || null };
  }
  if (payload?.status === "not_committed") {
    return { phase: "failed" };
  }
  return { phase: "unknown" };
}

export function settleSaveOutcome({ requestError, commitResult, refreshError }) {
  if (requestError && /historical records were saved/i.test(String(requestError.message || ""))) {
    return {
      phase: "saved",
      saved: true,
      canRetry: false,
      notice: safeSaveReason(requestError),
      error: "",
    };
  }
  if (requestError && isUncertainSaveError(requestError)) {
    return {
      phase: "unknown",
      saved: false,
      canRetry: false,
      notice: "",
      error: SAVE_UNKNOWN_MESSAGE,
    };
  }
  if (requestError || !commitResult?.saved) {
    const reason = requestError
      ? safeSaveReason(requestError)
      : (commitResult?.reasonText || "Blocking validation issues must be resolved before saving.");
    return {
      phase: "failed",
      saved: false,
      canRetry: true,
      notice: "",
      error: `${SAVE_FAILURE_MESSAGE}. ${reason}`,
    };
  }
  if (refreshError) {
    return {
      phase: "saved",
      saved: true,
      canRetry: false,
      notice: SAVE_REFRESH_FAILURE_MESSAGE,
      error: "",
    };
  }
  return {
    phase: "saved",
    saved: true,
    canRetry: false,
    notice: SAVE_SUCCESS_MESSAGE,
    error: "",
  };
}

export function outcomeFromConfirmationStatus(payload) {
  const interpreted = interpretConfirmationStatus(payload);
  if (interpreted.phase === "saved") {
    return {
      phase: "saved",
      saved: true,
      canRetry: false,
      notice: SAVE_SUCCESS_MESSAGE,
      error: "",
      result: interpreted.result,
    };
  }
  if (interpreted.phase === "failed") {
    return {
      phase: "failed",
      saved: false,
      canRetry: true,
      notice: "",
      error: `${SAVE_FAILURE_MESSAGE}. The historical master was not updated.`,
    };
  }
  return {
    phase: "unknown",
    saved: false,
    canRetry: false,
    notice: "",
    error: SAVE_UNKNOWN_MESSAGE,
  };
}

export function confirmButtonPresentation({ phase, blocked, saving }) {
  if (phase === "saving" || saving) {
    return { label: SAVING_LABEL, disabled: true, checkStatus: false };
  }
  if (phase === "saved") {
    return { label: SAVED_LABEL, disabled: true, checkStatus: false };
  }
  if (phase === "unknown") {
    return { label: "Confirm & Save", disabled: true, checkStatus: true };
  }
  return { label: "Confirm & Save", disabled: Boolean(blocked), checkStatus: false };
}

export async function commitReviewedUpload({
  previewId,
  reviewBody,
  token,
  completedPreviewId = "",
  stage,
  confirm,
  signal,
}) {
  if (!previewId) {
    throw new TypeError("Confirm & Save requires a preview id.");
  }
  if (completedPreviewId && completedPreviewId === previewId) {
    return { saved: false, reason: "already-saved", previewId };
  }
  if (!token) {
    return { saved: false, reason: "renew", previewId, submit: false };
  }
  let nextId = previewId;
  let stagedPreview = null;
  if (reviewBody) {
    stagedPreview = await stage(previewId, reviewBody, token, signal);
    if (!stagedPreview || stagedPreview.can_confirm !== true) {
      return { saved: false, reason: "unresolved", preview: stagedPreview, previewId };
    }
    nextId = stagedPreview.preview_id || previewId;
    if (completedPreviewId && completedPreviewId === nextId) {
      return { saved: false, reason: "already-saved", previewId: nextId, preview: stagedPreview };
    }
  }
  const result = await confirm(nextId, token, signal);
  return { saved: true, result, previewId: nextId, preview: stagedPreview };
}
