export const LOCK_MESSAGE = "Model training in progress. Please wait.";
export const CONFIRM_TEXT = "The system will be unavailable until training and model saving finish.";
export const READY_TEXT = "Training completed — ready to save";
export const SAVED_TEXT = "Models saved successfully";
export const JOB_STORAGE_KEY = "retrainJobId";
export const TOKEN_STORAGE_KEY = "retrainManageToken";

const ACTIVE_STATES = new Set(["QUEUED", "TRAINING", "VALIDATING", "READY_TO_SAVE", "SAVING", "SAVED"]);
const FAILURE_STATES = new Set(["FAILED", "INTERRUPTED", "ABANDONED", "CANCELLED"]);
const STAGE_LABELS = {
  queued: "Queued",
  loading: "Loading historical snapshot",
  preprocessing: "Preprocessing",
  evaluation: "Evaluating models",
  final_training: "Final training",
  validating: "Validating candidate",
  ready_to_save: "Ready to save",
  saving: "Saving models",
  saved: "Saved",
  training: "Training",
};

export function rememberRetrainSession(jobId, token, storage = globalThis.localStorage) {
  storage.setItem(JOB_STORAGE_KEY, jobId || "");
  storage.setItem(TOKEN_STORAGE_KEY, token || "");
}

export function readRetrainSession(storage = globalThis.localStorage) {
  return {
    jobId: storage.getItem(JOB_STORAGE_KEY) || "",
    token: storage.getItem(TOKEN_STORAGE_KEY) || "",
  };
}

export function clearRetrainSession(storage = globalThis.localStorage) {
  storage.removeItem(JOB_STORAGE_KEY);
  storage.removeItem(TOKEN_STORAGE_KEY);
}

export function modalDismissible(state) {
  return FAILURE_STATES.has(state);
}

export function escapeDismisses(state) {
  return modalDismissible(state);
}

export function backdropDismisses() {
  return false;
}

export function blocksNavigation(state) {
  return ACTIVE_STATES.has(state) || state === "SAVE_FAILED";
}

export function showSaveModels(state) {
  return state === "READY_TO_SAVE";
}

export function showSaveFailureActions(state) {
  return state === "SAVE_FAILED";
}

export function showTrainingFailureActions(state) {
  return FAILURE_STATES.has(state);
}

export function progressText(status) {
  if (!status) {
    return "";
  }
  const stage = STAGE_LABELS[status.progress_stage] || STAGE_LABELS[String(status.state || "").toLowerCase()] || status.progress_stage || status.state || "";
  const current = status.progress_current;
  const total = status.progress_total;
  const code = status.progress_budget_code ? ` · ${status.progress_budget_code}` : "";
  if (Number.isInteger(current) && Number.isInteger(total) && total > 0) {
    return `${stage} ${current} of ${total}${code}`;
  }
  return `${stage}${code}`;
}

export function shouldStickToBottom(scrollHeight, scrollTop, clientHeight, threshold = 24) {
  if (![scrollHeight, scrollTop, clientHeight].every(Number.isFinite)) {
    return true;
  }
  return scrollHeight - scrollTop - clientHeight <= threshold;
}

export function countdownSeconds(status, nowMs, fetchedAtMs = nowMs) {
  if (!status || status.state !== "SAVED" || !status.unlock_at) {
    return null;
  }
  const unlockAt = Date.parse(status.unlock_at);
  if (Number.isNaN(unlockAt)) {
    return null;
  }
  const serverNow = Date.parse(status.server_now);
  const offset = Number.isNaN(serverNow) ? 0 : serverNow - fetchedAtMs;
  return Math.max(0, Math.ceil((unlockAt - (nowMs + offset)) / 1000));
}

export function restoreRetrainPresentation(status, dismissedJobId, nowMs, fetchedAtMs = nowMs) {
  if (!status) {
    return { mode: "hidden", dismissible: false };
  }
  if (status.state === "SAVED" && countdownSeconds(status, nowMs, fetchedAtMs) === 0) {
    return { mode: "hidden", dismissible: false };
  }
  const readableState = ACTIVE_STATES.has(status.state) || FAILURE_STATES.has(status.state) || status.state === "SAVE_FAILED";
  if (readableState && dismissedJobId !== status.job_id) {
    return { mode: "modal", dismissible: modalDismissible(status.state) };
  }
  if (status.locked) {
    return { mode: "locked", dismissible: false };
  }
  return { mode: "hidden", dismissible: false };
}

export function failedResponseError(status, payload) {
  let message = `Request failed (${status})`;
  if (typeof payload?.detail === "string") {
    message = payload.detail;
  } else if (Array.isArray(payload?.detail)) {
    message = payload.detail.map((item) => item.msg || JSON.stringify(item)).join(" ");
  } else if (payload?.message) {
    message = payload.message;
  }
  const error = new Error(message);
  error.status = status;
  error.code = payload?.code || (status === 423 ? "SYSTEM_LOCKED" : undefined);
  return error;
}
