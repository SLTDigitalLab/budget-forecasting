const flows = new Map();

export const UPLOAD_FLOW = "upload";
export const MASTER_EDIT_FLOW = "master-edit";
export const RETRAIN_FLOW = "retrain";
export const ROLLBACK_FLOW = "rollback";
export const BOOK_ENTRY_FLOW = "book-entry";

export function grantFlowAuthorization(name, token, expiresAt, now = Date.now()) {
  const value = String(token || "");
  const expiry = Number(expiresAt);
  if (!value || !Number.isFinite(expiry) || expiry <= now) {
    flows.delete(name);
    return false;
  }
  flows.set(name, { token: value, expiresAt: expiry });
  return true;
}

export function readFlowAuthorization(name, now = Date.now()) {
  const entry = flows.get(name);
  if (!entry || entry.expiresAt <= now) {
    flows.delete(name);
    return "";
  }
  return entry.token;
}

export function clearFlowAuthorization(name) {
  flows.delete(name);
}

export function clearAllFlowAuthorizations() {
  flows.clear();
}
