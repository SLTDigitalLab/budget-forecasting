export const LEGACY_LOGIN_PATH = "/login";
export const AUTH_RETURN_KEY = "auth_return_path";

const PAGE_FRAMES = {
  "/": { title: "Overview", summary: "" },
  "/generate-forecast": {
    title: "Generate Forecast",
    summary: "Select a budget category and forecast period to generate predictions.",
  },
  "/analytics": { title: "Analytics & Charts", summary: "" },
  "/export": {
    title: "Export Forecasts",
    summary: "Preview a completed forecast report, then download PDF or Excel from the stored run.",
  },
  "/settings": { title: "Settings", summary: "Manage historical financial data." },
  "/forecast-history": {
    title: "Forecast History",
    summary: "System-wide forecast records saved after each successful Generate Forecast run.",
  },
};

export function signedOutRoute(pathname) {
  const path = pathname || "/";
  if (path === LEGACY_LOGIN_PATH) {
    return { pathname: "/", replace: true, showOverlay: true };
  }
  return { pathname: path, replace: false, showOverlay: true };
}

export function routeAfterLogout(pathname) {
  const path = pathname && pathname !== LEGACY_LOGIN_PATH ? pathname : "/";
  return { pathname: path, navigateToLogin: false, showOverlay: true };
}

export function routeAfterLogin(returnPath) {
  const path = returnPath && returnPath !== LEGACY_LOGIN_PATH ? returnPath : "/";
  return { pathname: path, showOverlay: false };
}

export function shouldMountProtectedPage(isAuthenticated) {
  return Boolean(isAuthenticated);
}

export function pageFrameFor(pathname) {
  return PAGE_FRAMES[pathname] || PAGE_FRAMES["/"];
}

export function rememberAuthReturn(pathname, storage) {
  if (!storage) {
    return;
  }
  const path = pathname && pathname !== LEGACY_LOGIN_PATH ? pathname : "";
  if (!path || path === "/") {
    storage.removeItem(AUTH_RETURN_KEY);
    return;
  }
  storage.setItem(AUTH_RETURN_KEY, path);
}

export function consumeAuthReturn(storage) {
  if (!storage) {
    return "";
  }
  const value = storage.getItem(AUTH_RETURN_KEY) || "";
  storage.removeItem(AUTH_RETURN_KEY);
  if (!value || value === "/" || value === LEGACY_LOGIN_PATH) {
    return "";
  }
  return value;
}
