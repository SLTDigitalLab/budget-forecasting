import { getAuthConfigError, readAuthConfig } from "./authConfig.js";
import {
  API_AUTH_STATE_KEY,
  API_TOKEN_MESSAGE,
  GRAPH_SCOPE,
  buildAuthorizeUrl,
  createRandomValue,
} from "./microsoftLogin.js";

let cached = null;

export function apiScopeConfigError(env = import.meta.env) {
  return getAuthConfigError(env);
}

export function cachedApiAccessToken(now = Date.now()) {
  if (!cached || cached.expiresAt <= now + 30000) {
    return "";
  }
  return cached.token;
}

export function clearApiAccessToken() {
  cached = null;
}

export function rememberApiAccessToken(token, expiresIn, now = Date.now()) {
  const seconds = Number(expiresIn);
  const lifetime = Number.isFinite(seconds) && seconds > 0 ? seconds : 3600;
  cached = { token: String(token || ""), expiresAt: now + lifetime * 1000 };
  return cached.token;
}

export function runAuthorizedAction(mutate, acquire = acquireActionAccessToken) {
  return Promise.resolve()
    .then(() => acquire())
    .then((token) => mutate(token));
}

export function acquireActionAccessToken(options = {}) {
  return openApiTokenPopup(options);
}

export function acquireApiAccessToken(options = {}) {
  return openApiTokenPopup(options);
}

function openApiTokenPopup({
  env = import.meta.env,
  location = typeof window !== "undefined" ? window.location : { origin: "http://localhost" },
  storage = typeof localStorage !== "undefined" ? localStorage : null,
  openWindow = typeof window !== "undefined" ? window.open.bind(window) : null,
  listen = typeof window !== "undefined" ? window : null,
} = {}) {
  const configError = getAuthConfigError(env);
  if (configError) {
    return Promise.reject(new Error(configError));
  }
  const { tenantId, clientId } = readAuthConfig(env);
  const scope = GRAPH_SCOPE;
  const state = createRandomValue();
  const nonce = createRandomValue();
  const durableStore = typeof localStorage !== "undefined" ? localStorage : storage;
  durableStore?.setItem(API_AUTH_STATE_KEY, state);
  const url = buildAuthorizeUrl({
    tenantId,
    clientId,
    redirectUri: location.origin,
    state,
    nonce,
    scope,
    prompt: "select_account",
  });
  const popup = openWindow ? openWindow(url, "retrainApiAuth", "popup=yes,width=520,height=720") : null;
  if (!popup) {
    durableStore?.removeItem(API_AUTH_STATE_KEY);
    return Promise.reject(new Error("Microsoft sign-in could not open. Allow popups for this site, then try again."));
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = listen?.setTimeout?.(() => {
      finish(new Error("Microsoft did not return a Graph access token."));
    }, 120000);
    const watcher = listen?.setInterval?.(() => {
      if (popup.closed) {
        finish(new Error("Microsoft sign-in was cancelled."));
      }
    }, 300);
    function onMessage(event) {
      if (event.origin !== location.origin || event.data?.type !== API_TOKEN_MESSAGE) {
        return;
      }
      const token = String(event.data.accessToken || "");
      if (!token) {
        finish(new Error("Microsoft did not return a Graph access token."));
        return;
      }
      finish(null, token);
    }
    function finish(error, token) {
      if (settled) {
        return;
      }
      settled = true;
      if (timer) listen?.clearTimeout?.(timer);
      if (watcher) listen?.clearInterval?.(watcher);
      listen?.removeEventListener?.("message", onMessage);
      if (error) {
        reject(error);
        return;
      }
      resolve(token);
    }
    listen?.addEventListener?.("message", onMessage);
  });
}
