function isUnset(value) {
  const text = String(value || "").trim();
  return !text || /YOUR_[A-Z0-9_]+/i.test(text);
}

export function runtimeRedirectUri() {
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin;
  }
  return "http://localhost";
}

export function readAuthConfig(env = import.meta.env) {
  const tenantId = String(env?.VITE_AZURE_TENANT_ID || "").trim();
  const clientId = String(env?.VITE_AZURE_CLIENT_ID || "").trim();
  const missing = [];
  if (isUnset(tenantId)) missing.push("VITE_AZURE_TENANT_ID");
  if (isUnset(clientId)) missing.push("VITE_AZURE_CLIENT_ID");
  return {
    tenantId,
    clientId,
    redirectUri: runtimeRedirectUri(),
    missing,
  };
}

export function getAuthConfigError(env = import.meta.env) {
  const config = readAuthConfig(env);
  if (config.missing.length) {
    return `Microsoft authentication is not configured. Missing ${config.missing.join(", ")}.`;
  }
  return "";
}

export function formatAuthStartupError(cause) {
  const message = String(cause?.errorMessage || cause?.message || cause || "").trim();
  if (/AADSTS50011/i.test(message) || /redirect.?uri/i.test(message)) {
    return "Microsoft rejected this app's redirect URL. Ask an administrator to add http://localhost:5174 as a Single-page application redirect URI. The Tenant ID and Client ID are unchanged.";
  }
  if (message) {
    return `Microsoft authentication could not start. ${message}`;
  }
  return "Microsoft authentication could not start. Retry, or check that this origin is registered in Microsoft Entra.";
}
