import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { getHealth, getOverviewCategories } from "../api/forecastApi.js";
import { getAuthConfigError, readAuthConfig, runtimeRedirectUri } from "../auth/authConfig.js";
import {
  API_AUTH_STATE_KEY,
  API_TOKEN_MESSAGE,
  AUTH_STATE_KEY,
  AUTH_USER_KEY,
  GRAPH_ME_URL,
  GRAPH_SCOPE,
  buildAuthorizeUrl,
  clearLoginSession,
  completeOAuthCallback,
  displayNameFromUser,
  formatCallbackError,
  mapGraphProfile,
  parseOAuthHash,
  resetOAuthCallbackJob,
  startMicrosoftLogin,
  validateOAuthState,
} from "../auth/microsoftLogin.js";

const here = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(here, "../App.jsx"), "utf8");
const loginSource = readFileSync(resolve(here, "../pages/Login.jsx"), "utf8");
const apiSource = readFileSync(resolve(here, "../api/forecastApi.js"), "utf8");
const sidebarSource = readFileSync(resolve(here, "../components/Sidebar.jsx"), "utf8");
const contextSource = readFileSync(resolve(here, "../context/ForecastContext.jsx"), "utf8");
const authContextSource = readFileSync(resolve(here, "../auth/AuthContext.jsx"), "utf8");
const loginLibSource = readFileSync(resolve(here, "../auth/microsoftLogin.js"), "utf8");
const dockerSource = readFileSync(resolve(here, "../../../docker-compose.yml"), "utf8");
const frontendDocker = readFileSync(resolve(here, "../../Dockerfile"), "utf8");
const packageJson = readFileSync(resolve(here, "../../package.json"), "utf8");
const envExample = readFileSync(resolve(here, "../../../.env.example"), "utf8");
const docs = readFileSync(resolve(here, "../../../docs/entra-setup.md"), "utf8");
const viteSource = readFileSync(resolve(here, "../../vite.config.js"), "utf8");
const mainSource = readFileSync(resolve(here, "../main.jsx"), "utf8");

const validEnv = {
  VITE_AZURE_TENANT_ID: "11111111-1111-1111-1111-111111111111",
  VITE_AZURE_CLIENT_ID: "22222222-2222-2222-2222-222222222222",
};

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
    },
    setItem(key, value) {
      data[key] = String(value);
    },
    removeItem(key) {
      delete data[key];
    },
    dump() {
      return data;
    },
  };
}

test("missing Microsoft auth configuration is a visible error", () => {
  const error = getAuthConfigError({});
  assert.match(error, /not configured/i);
  assert.match(error, /VITE_AZURE_TENANT_ID/);
  assert.match(error, /VITE_AZURE_CLIENT_ID/);
  assert.doesNotMatch(error, /VITE_AZURE_REDIRECT_URI/);
  assert.doesNotMatch(error, /VITE_AZURE_API_SCOPE/);
  assert.doesNotMatch(envExample, /VITE_AZURE_API_SCOPE/);
  assert.doesNotMatch(envExample, /AZURE_API_AUDIENCE/);
  assert.doesNotMatch(envExample, /AZURE_CLIENT_SECRET/);
  assert.doesNotMatch(envExample, /AZURE_APPROVED_OBJECT_IDS/);
  assert.equal(getAuthConfigError(validEnv), "");
});

test("placeholder Entra values are treated as missing configuration", () => {
  const error = getAuthConfigError({
    VITE_AZURE_TENANT_ID: "YOUR_TENANT_ID",
    VITE_AZURE_CLIENT_ID: "YOUR_SPA_APP_CLIENT_ID",
  });
  assert.match(error, /not configured/i);
});

test("authorize URL matches Vision Flow implicit token + Graph User.Read", () => {
  const redirectUri = "http://localhost:5174";
  const url = buildAuthorizeUrl({
    tenantId: validEnv.VITE_AZURE_TENANT_ID,
    clientId: validEnv.VITE_AZURE_CLIENT_ID,
    redirectUri,
    state: "state123",
    nonce: "nonce123",
  });
  assert.match(url, /https:\/\/login\.microsoftonline\.com\/11111111-1111-1111-1111-111111111111\/oauth2\/v2\.0\/authorize\?/);
  assert.match(url, /response_type=token/);
  assert.match(url, /response_mode=fragment/);
  assert.match(url, new RegExp(`scope=${encodeURIComponent(GRAPH_SCOPE)}`));
  assert.match(url, new RegExp(`redirect_uri=${encodeURIComponent(redirectUri)}`));
  assert.match(url, /state=state123/);
  assert.equal(readAuthConfig(validEnv).redirectUri, runtimeRedirectUri());
  assert.doesNotMatch(envExample, /VITE_AZURE_API_SCOPE/);
  assert.doesNotMatch(envExample, /VITE_AZURE_REDIRECT_URI/);
});

test("startMicrosoftLogin stores OAuth state and redirects with the current origin", () => {
  const storage = memoryStorage();
  const location = { href: "", origin: "http://localhost:5174" };
  startMicrosoftLogin({ env: validEnv, location, storage });
  const state = storage.getItem(AUTH_STATE_KEY);
  assert.ok(state);
  assert.match(location.href, /response_type=token/);
  assert.match(location.href, new RegExp(`redirect_uri=${encodeURIComponent("http://localhost:5174")}`));
  assert.match(location.href, new RegExp(`state=${state}`));
});

test("OAuth callback validates state and does not fetch the profile on mismatch", async () => {
  resetOAuthCallbackJob();
  let fetched = false;
  const storage = memoryStorage({ [AUTH_STATE_KEY]: "expected-state" });
  const history = { replaceState() {} };
  const result = await completeOAuthCallback({
    hash: "#access_token=secret-token&state=other-state&token_type=Bearer",
    pathname: "/",
    storage,
    history,
    fetchProfile: async () => {
      fetched = true;
      return { name: "Should not run" };
    },
  });
  assert.equal(fetched, false);
  assert.match(result.error, /state/i);
  assert.equal(result.user, null);
  assert.equal(storage.getItem(AUTH_STATE_KEY), null);
});

test("OAuth callback stores the Graph profile and does not persist the access token", async () => {
  const storage = memoryStorage({ [AUTH_STATE_KEY]: "ok-state" });
  const historyCalls = [];
  const result = await completeOAuthCallback({
    hash: "#access_token=secret-token&state=ok-state",
    pathname: "/",
    storage,
    history: {
      replaceState(_state, _title, url) {
        historyCalls.push(url);
      },
    },
    fetchProfile: async (token) => {
      assert.equal(token, "secret-token");
      return mapGraphProfile({
        displayName: "Ada Lovelace",
        mail: "ada@example.com",
        id: "graph-id",
      });
    },
  });
  assert.equal(result.error, "");
  assert.equal(result.user.name, "Ada Lovelace");
  assert.equal(JSON.parse(storage.getItem(AUTH_USER_KEY)).email, "ada@example.com");
  assert.equal(storage.getItem(AUTH_STATE_KEY), null);
  assert.doesNotMatch(JSON.stringify(storage.dump()), /secret-token/);
  assert.deepEqual(historyCalls, ["/"]);
});

test("an API-scope callback returns the token to the opener and does not store it", async () => {
  const storage = memoryStorage({ [API_AUTH_STATE_KEY]: "api-state" });
  const messages = [];
  const previousWindow = globalThis.window;
  globalThis.window = {
    opener: {
      postMessage(data) {
        messages.push(data);
      },
    },
    location: { origin: "http://localhost:5174" },
  };
  try {
    const result = await completeOAuthCallback({
      hash: "#access_token=secret-api-token&state=api-state&expires_in=120",
      pathname: "/settings",
      storage,
      history: { replaceState() {} },
      fetchProfile: async () => {
        throw new Error("Graph profile must not be loaded for the API token");
      },
    });
    assert.equal(result.error, "");
    assert.equal(messages[0].type, API_TOKEN_MESSAGE);
    assert.equal(messages[0].accessToken, "secret-api-token");
    assert.equal(storage.getItem(AUTH_USER_KEY), null);
    assert.equal(storage.getItem(API_AUTH_STATE_KEY), null);
    assert.equal(result.actionAccessToken, "secret-api-token");
    assert.doesNotMatch(JSON.stringify(storage.dump()), /secret-api-token/);
  } finally {
    globalThis.window = previousWindow;
  }
});

test("a same-tab action return keeps the app session and does not store the token", async () => {
  const storage = memoryStorage({
    [API_AUTH_STATE_KEY]: "api-state",
    [AUTH_USER_KEY]: JSON.stringify({ name: "Ada", email: "ada@example.com", id: "graph-id" }),
  });
  const previousWindow = globalThis.window;
  globalThis.window = { opener: null, location: { origin: "http://localhost" } };
  try {
    const result = await completeOAuthCallback({
      hash: "#access_token=secret-api-token&state=api-state&expires_in=120",
      pathname: "/settings",
      storage,
      history: { replaceState() {} },
      fetchProfile: async () => {
        throw new Error("Graph profile must not be loaded for the action token");
      },
    });
    assert.equal(result.actionAttempt, true);
    assert.equal(result.actionAccessToken, "secret-api-token");
    assert.equal(result.user.name, "Ada");
    assert.doesNotMatch(JSON.stringify(storage.dump()), /secret-api-token/);
  } finally {
    globalThis.window = previousWindow;
  }
});

test("login cancellation and profile failures are readable", async () => {
  assert.match(formatCallbackError("access_denied", "the user cancelled"), /cancelled/i);
  const storage = memoryStorage({ [AUTH_STATE_KEY]: "ok-state" });
  const profileFail = await completeOAuthCallback({
    hash: "#access_token=secret-token&state=ok-state",
    storage,
    history: { replaceState() {} },
    fetchProfile: async () => {
      throw new Error("graph down");
    },
  });
  assert.match(profileFail.error, /profile/i);
  assert.match(profileFail.error, /Retry/i);
  const cancelled = await completeOAuthCallback({
    hash: "#error=access_denied&error_description=AADSTS65004",
    storage: memoryStorage({ [AUTH_STATE_KEY]: "x" }),
    history: { replaceState() {} },
  });
  assert.match(cancelled.error, /cancelled/i);
});

test("protected financial routes require the frontend login session", () => {
  const protectedRouteSource = readFileSync(resolve(here, "../auth/ProtectedRoute.jsx"), "utf8");
  assert.match(appSource, /ProtectedRoute/);
  assert.match(appSource, /AuthProvider/);
  assert.match(appSource, /path="\/login" element=\{<Navigate to="\/" replace \/>\}/);
  assert.doesNotMatch(protectedRouteSource, /to="\/login"/);
  assert.match(loginSource, /Welcome to/);
  assert.match(loginSource, /AI Budget Forecasting System/);
  assert.match(loginSource, /Please authenticate to continue/);
  assert.doesNotMatch(loginSource, /Sign in with your Microsoft work account to continue/);
  assert.match(loginSource, /Sign in with Microsoft/);
  assert.match(loginSource, /onClick=\{error \? retry : login\}/);
  assert.match(protectedRouteSource, /login-app-preview/);
  assert.match(protectedRouteSource, /inert=\{signedOut \? "" : undefined\}/);
  assert.match(loginSource, /login-scrim/);
  assert.match(loginSource, /login-microsoft-mark/);
  assert.doesNotMatch(loginSource, /className="generate-button login-microsoft-button"/);
  assert.match(loginSource, /Retry/);
  assert.match(appSource, /<GuardedPage><Overview \/><\/GuardedPage>/);
  assert.match(appSource, /generate-forecast/);
  assert.match(appSource, /analytics/);
  assert.match(appSource, /export/);
  assert.match(appSource, /forecast-history/);
  assert.match(appSource, /settings/);
  assert.doesNotMatch(appSource, /sessionStorage\.setItem\(\s*["']user["']/);
  assert.match(loginLibSource, /azureUser/);
  assert.equal(displayNameFromUser({ name: "Ada Lovelace" }), "Ada Lovelace");
});

test("logout clears the local login session and forecast state", () => {
  assert.match(contextSource, /resetSession/);
  assert.match(sidebarSource, /resetSession\(\)/);
  assert.match(sidebarSource, /logout\(\)/);
  assert.match(sidebarSource, /SidebarProfile/);
  assert.match(authContextSource, /clearLoginSession/);
  assert.doesNotMatch(authContextSource, /navigate\(\s*["']\/login["']\s*\)/);
  const storage = memoryStorage({
    [AUTH_USER_KEY]: JSON.stringify({ name: "Ada" }),
    [AUTH_STATE_KEY]: "leftover",
  });
  clearLoginSession(storage);
  assert.equal(storage.getItem(AUTH_USER_KEY), null);
  assert.equal(storage.getItem(AUTH_STATE_KEY), null);
});

test("forecast API calls remain without Graph tokens", async () => {
  const previous = globalThis.fetch;
  let headers = null;
  globalThis.fetch = async (url, options = {}) => {
    headers = options.headers || {};
    return { ok: true, json: async () => ({ ok: true, status: "healthy" }) };
  };
  try {
    await getOverviewCategories();
    assert.equal(headers.Authorization, undefined);
    await getHealth();
    assert.equal(headers.Authorization, undefined);
  } finally {
    globalThis.fetch = previous;
  }
  assert.doesNotMatch(apiSource, /Bearer \$\{token\}/);
  assert.doesNotMatch(packageJson, /@azure\/msal-react/);
  assert.doesNotMatch(mainSource, /MsalProvider/);
  assert.doesNotMatch(appSource, /MsalProvider/);
  assert.doesNotMatch(dockerSource, /AZURE_APPROVED_OBJECT_IDS/);
  assert.doesNotMatch(dockerSource, /VITE_AZURE_API_SCOPE/);
  assert.doesNotMatch(dockerSource, /AZURE_API_AUDIENCE/);
  assert.doesNotMatch(dockerSource, /AZURE_CLIENT_SECRET/);
  assert.match(frontendDocker, /ARG VITE_AZURE_TENANT_ID/);
  assert.match(frontendDocker, /ARG VITE_AZURE_CLIENT_ID/);
  assert.doesNotMatch(frontendDocker, /VITE_AZURE_API_SCOPE/);
  assert.ok(loginLibSource.includes(GRAPH_ME_URL));
  assert.doesNotMatch(loginLibSource, /console\.log\([^)]*accessToken/);
  assert.doesNotMatch(authContextSource, /console\.log\([^)]*token/i);
});

test("local Vite binds 5174 with a strict port and proxies /api to FastAPI 8000", () => {
  assert.match(viteSource, /port:\s*5174/);
  assert.match(viteSource, /strictPort:\s*true/);
  assert.match(viteSource, /["']\/api["']:\s*["']http:\/\/localhost:8000["']/);
  assert.match(docs, /http:\/\/localhost:5174/);
  const parsed = parseOAuthHash("#access_token=x&state=y");
  assert.equal(parsed.accessToken, "x");
  assert.equal(validateOAuthState("y", "y"), "");
});
