import { getAuthConfigError, readAuthConfig } from "./authConfig.js";
import {
  API_AUTH_STATE_KEY,
  GRAPH_SCOPE,
  buildAuthorizeUrl,
  createRandomValue,
} from "./microsoftLogin.js";
import { rememberAuthReturn } from "./authRouting.js";

export const ACTION_RESUME_KEY = "settings_action_resume";
export const ACTION_PROMPT = "Please authenticate to make changes";

const CONFIRMATION_KINDS = new Set([
  "begin-retrain",
  "ask-retrain",
  "open-rollback",
  "review-rollback",
  "commit-rollback",
  "add-book-entry",
  "edit-book-entry",
  "delete-book-entry",
]);

let returnedToken = "";
let returnedExpiresAt = 0;
let actionFailure = null;
const promptListeners = new Set();
let activePrompt = null;

function notifyPrompt() {
  promptListeners.forEach((listener) => listener(activePrompt));
}

export function subscribeActionPrompt(listener) {
  promptListeners.add(listener);
  listener(activePrompt);
  return () => {
    promptListeners.delete(listener);
  };
}

function resumePayload(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  return Object.fromEntries(
    Object.entries(source).filter(([key]) => !/token|authorization|secret/i.test(key)),
  );
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

export function jsonSafeData(value, seen = new WeakSet()) {
  if (value == null) {
    return value;
  }
  if (typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TypeError("Settings actions cannot store non-finite numbers.");
    }
    return value;
  }
  if (typeof value !== "object") {
    throw new TypeError("Settings actions cannot store functions or non-JSON values.");
  }
  if (
    seen.has(value)
    || value.nodeType != null
    || value.nativeEvent
    || Object.keys(value).some((key) => key.startsWith("__reactFiber") || key === "stateNode")
  ) {
    throw new TypeError("Settings actions cannot store a React event, DOM node, or circular value.");
  }
  if (!Array.isArray(value) && !isPlainObject(value)) {
    throw new TypeError("Settings actions cannot store a React event, DOM node, or circular value.");
  }
  seen.add(value);
  if (Array.isArray(value)) {
    return value.map((item) => jsonSafeData(item, seen));
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, jsonSafeData(item, seen)]),
  );
}

export function saveActionResume(descriptor, storage) {
  const store = storage || sessionStorage;
  const record = {
    kind: String(descriptor?.kind || ""),
    destructive: Boolean(descriptor?.destructive),
    payload: jsonSafeData(resumePayload(descriptor?.payload)),
  };
  store.setItem(ACTION_RESUME_KEY, JSON.stringify(record));
  return record;
}

export function readActionResume(storage) {
  const store = storage || sessionStorage;
  try {
    const raw = store.getItem(ACTION_RESUME_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    if (!parsed?.kind) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function rememberReturnedActionToken(token, expiresIn, now = Date.now()) {
  returnedToken = String(token || "");
  const seconds = Number(expiresIn);
  returnedExpiresAt = returnedToken && Number.isFinite(seconds) && seconds > 0 ? now + seconds * 1000 : 0;
}

export function claimReturnedActionToken() {
  const token = returnedToken;
  returnedToken = "";
  return token;
}

export function takeReturnedActionExpiry() {
  const expiresAt = returnedExpiresAt;
  returnedExpiresAt = 0;
  return expiresAt;
}

export function takeActionFailure() {
  const failure = actionFailure;
  actionFailure = null;
  return failure;
}

export function requestSettingsAction(descriptor) {
  activePrompt = {
    kind: descriptor.kind,
    destructive: Boolean(descriptor.destructive),
    payload: jsonSafeData(resumePayload(descriptor?.payload)),
  };
  notifyPrompt();
  return Promise.resolve({ started: false, prompt: true });
}

export function cancelSettingsAction(storage, message = "") {
  const store = storage || sessionStorage;
  const resume = readActionResume(store);
  const kind = activePrompt?.kind || resume?.kind || "";
  actionFailure = message ? { kind, message } : null;
  activePrompt = null;
  notifyPrompt();
  store.removeItem(ACTION_RESUME_KEY);
}

export function startSettingsActionRedirect({
  descriptor,
  env = import.meta.env,
  location = window.location,
  sessionStore = sessionStorage,
  localStore = localStorage,
} = {}) {
  const configError = getAuthConfigError(env);
  if (configError) {
    throw new Error(configError);
  }
  saveActionResume(descriptor, sessionStore);
  const { tenantId, clientId } = readAuthConfig(env);
  const state = createRandomValue();
  const nonce = createRandomValue();
  localStore.setItem(API_AUTH_STATE_KEY, state);
  rememberAuthReturn(location.pathname || "/settings", sessionStore);
  const url = buildAuthorizeUrl({
    tenantId,
    clientId,
    redirectUri: location.origin,
    state,
    nonce,
    scope: GRAPH_SCOPE,
    prompt: "select_account",
  });
  location.href = url;
  activePrompt = null;
  notifyPrompt();
  return url;
}

export function consumeSettingsActionResume(storage, kinds) {
  const store = storage || sessionStorage;
  const resume = readActionResume(store);
  if (!resume) {
    return null;
  }
  if (kinds && !kinds.has(resume.kind)) {
    return null;
  }
  store.removeItem(ACTION_RESUME_KEY);
  const token = returnedToken;
  if (!token) {
    if (resume.destructive && CONFIRMATION_KINDS.has(resume.kind)) {
      return { mode: "restore", resume };
    }
    return { mode: "drop", resume };
  }
  return { mode: "run", resume };
}
