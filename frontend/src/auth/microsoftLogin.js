import { getAuthConfigError, readAuthConfig, runtimeRedirectUri } from "./authConfig.js";

export const AUTH_USER_KEY = "azureUser";
export const AUTH_STATE_KEY = "auth_state";
export const API_AUTH_STATE_KEY = "api_auth_state";
export const API_TOKEN_MESSAGE = "retrain-api-token";
export const GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me";
export const GRAPH_SCOPE = "User.Read";

let inflightCallback = null;

export function createRandomValue() {
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}

export function buildAuthorizeUrl({ tenantId, clientId, redirectUri, state, nonce, scope = GRAPH_SCOPE, prompt = "" }) {
  const AUTHORITY = `https://login.microsoftonline.com/${tenantId}`;
  const promptQuery = prompt ? `&prompt=${encodeURIComponent(prompt)}` : "";
  return (
    `${AUTHORITY}/oauth2/v2.0/authorize?` +
    `client_id=${clientId}` +
    `&response_type=token` +
    `&redirect_uri=${encodeURIComponent(redirectUri)}` +
    `&scope=${encodeURIComponent(scope)}` +
    promptQuery +
    `&response_mode=fragment` +
    `&state=${state}` +
    `&nonce=${nonce}`
  );
}

export function startMicrosoftLogin({
  env = import.meta.env,
  location = window.location,
  storage = sessionStorage,
} = {}) {
  const configError = getAuthConfigError(env);
  if (configError) {
    throw new Error(configError);
  }
  const { tenantId, clientId } = readAuthConfig(env);
  const redirectUri = location?.origin || runtimeRedirectUri();
  const state = createRandomValue();
  const nonce = createRandomValue();
  storage.setItem(AUTH_STATE_KEY, state);
  location.href = buildAuthorizeUrl({
    tenantId,
    clientId,
    redirectUri,
    state,
    nonce,
  });
}

export function parseOAuthHash(hash) {
  if (!hash || hash === "#") {
    return { empty: true, accessToken: "", error: "", errorDescription: "", state: "" };
  }
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  return {
    empty: false,
    accessToken: params.get("access_token") || "",
    expiresIn: params.get("expires_in") || "",
    error: params.get("error") || "",
    errorDescription: params.get("error_description") || "",
    state: params.get("state") || "",
  };
}

export function validateOAuthState(returned, expected) {
  if (!expected || !returned || returned !== expected) {
    return "Microsoft sign-in could not be verified because the login state did not match. Retry to sign in again.";
  }
  return "";
}

export function formatCallbackError(error, description = "") {
  const code = String(error || "").trim();
  const detail = String(description || "")
    .replace(/\+/g, " ")
    .trim();
  if (/access_denied/i.test(code) || /cancel/i.test(code) || /cancel/i.test(detail)) {
    return "Microsoft sign-in was cancelled. Retry when you are ready to continue.";
  }
  if (/AADSTS50011/i.test(detail) || /redirect.?uri/i.test(detail)) {
    return "Microsoft rejected this app's redirect URL. Ask an administrator to add http://localhost:5174 as a Single-page application redirect URI. The Tenant ID and Client ID are unchanged.";
  }
  if (detail) {
    return `Microsoft sign-in could not be completed. ${detail}`;
  }
  if (code) {
    return `Microsoft sign-in could not be completed. ${code}`;
  }
  return "Microsoft sign-in could not be completed. Retry to try again.";
}

export function formatProfileError() {
  return "Microsoft could not load your profile. Retry to sign in again.";
}

export function mapGraphProfile(userData) {
  return {
    name: userData?.displayName || "",
    email: userData?.mail || userData?.userPrincipalName || "",
    id: userData?.id || "",
  };
}

export function displayNameFromUser(user) {
  return user?.name || user?.email || "Signed-in user";
}

export function activityActorFromUser(user) {
  return {
    name: String(user?.name || "").trim(),
    email: String(user?.email || "").trim(),
  };
}

export function readStoredUser(storage) {
  const store = storage || (typeof sessionStorage !== "undefined" ? sessionStorage : null);
  if (!store) {
    return null;
  }
  try {
    const raw = store.getItem(AUTH_USER_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    if (!parsed || (!parsed.name && !parsed.email && !parsed.id)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearLoginSession(storage) {
  const store = storage || (typeof sessionStorage !== "undefined" ? sessionStorage : null);
  store?.removeItem(AUTH_USER_KEY);
  store?.removeItem(AUTH_STATE_KEY);
}

export async function fetchMicrosoftProfile(accessToken, fetchImpl = fetch) {
  const response = await fetchImpl(GRAPH_ME_URL, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
  });
  if (!response.ok) {
    throw new Error(formatProfileError());
  }
  const userData = await response.json();
  const profile = mapGraphProfile(userData);
  if (!profile.name && !profile.email) {
    throw new Error(formatProfileError());
  }
  return profile;
}

function stripOAuthHash(history, pathname) {
  history?.replaceState?.({}, typeof document !== "undefined" ? document.title : "", pathname || "/");
}

export async function completeOAuthCallback({
  hash,
  pathname,
  storage,
  history,
  fetchProfile = fetchMicrosoftProfile,
} = {}) {
  const locationHash = hash ?? (typeof window !== "undefined" ? window.location.hash : "");
  const nextPath = pathname ?? (typeof window !== "undefined" ? window.location.pathname : "/");
  const store = storage || (typeof sessionStorage !== "undefined" ? sessionStorage : null);
  const browserHistory = history || (typeof window !== "undefined" ? window.history : null);
  const parsed = parseOAuthHash(locationHash);

  if (parsed.accessToken || parsed.error) {
    stripOAuthHash(browserHistory, nextPath);
  }

  const localStore = typeof localStorage !== "undefined" ? localStorage : null;
  const apiExpected = localStore?.getItem(API_AUTH_STATE_KEY) || store?.getItem(API_AUTH_STATE_KEY) || "";
  const actionReturn = Boolean(apiExpected) && parsed.state === apiExpected;

  if (parsed.error) {
    if (actionReturn) {
      localStore?.removeItem(API_AUTH_STATE_KEY);
      store?.removeItem(API_AUTH_STATE_KEY);
      return {
        user: readStoredUser(store),
        error: formatCallbackError(parsed.error, parsed.errorDescription),
        actionAttempt: true,
        actionAccessToken: "",
        actionExpiresIn: "",
      };
    }
    store?.removeItem(AUTH_STATE_KEY);
    return { user: null, error: formatCallbackError(parsed.error, parsed.errorDescription) };
  }

  if (parsed.accessToken) {
    if (actionReturn) {
      localStore?.removeItem(API_AUTH_STATE_KEY);
      store?.removeItem(API_AUTH_STATE_KEY);
      const opener = typeof window !== "undefined" ? window.opener : null;
      const targetOrigin = typeof window !== "undefined" ? window.location.origin : "*";
      opener?.postMessage(
        {
          type: API_TOKEN_MESSAGE,
          accessToken: parsed.accessToken,
          expiresIn: parsed.expiresIn,
        },
        targetOrigin,
      );
      return {
        user: readStoredUser(store),
        error: "",
        actionAttempt: true,
        actionAccessToken: parsed.accessToken,
        actionExpiresIn: parsed.expiresIn,
      };
    }
    const expected = store?.getItem(AUTH_STATE_KEY) || "";
    store?.removeItem(AUTH_STATE_KEY);
    const stateError = validateOAuthState(parsed.state, expected);
    if (stateError) {
      return { user: null, error: stateError };
    }
    try {
      const user = await fetchProfile(parsed.accessToken);
      store?.setItem(AUTH_USER_KEY, JSON.stringify(user));
      return { user, error: "" };
    } catch {
      return { user: null, error: formatProfileError() };
    }
  }

  return { user: readStoredUser(store), error: "" };
}

export function consumeOAuthCallback(options) {
  if (!inflightCallback) {
    inflightCallback = completeOAuthCallback(options);
  }
  return inflightCallback;
}

export function resetOAuthCallbackJob() {
  inflightCallback = null;
}
