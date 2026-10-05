export const FILE_AUTH_READY = "Authorized — select a file to continue";
export const FILE_AUTH_EXPIRED = "File selection authorization expired. Authenticate again to choose a file.";
export const FILE_AUTH_CANCELLED = "Microsoft sign-in was cancelled. Retry when you are ready to continue.";
export const FILE_AUTH_DENIED = "You do not have permission to make these changes.";

const resetListeners = new Set();

export function initialFileSelection() {
  return {
    chooseEnabled: true,
    selectEnabled: false,
    pending: false,
    message: "",
    expiresAt: 0,
  };
}

export function beginFileAuthentication(state) {
  if (!state || state.pending || !state.chooseEnabled) {
    return { started: false, state: state || initialFileSelection() };
  }
  return {
    started: true,
    state: {
      chooseEnabled: false,
      selectEnabled: false,
      pending: true,
      message: "",
      expiresAt: 0,
    },
  };
}

export function fileAuthenticationSucceeded(expiresAt, now = Date.now()) {
  if (!expiresAt || expiresAt <= now) {
    return fileAuthenticationFailed(FILE_AUTH_EXPIRED);
  }
  return {
    chooseEnabled: false,
    selectEnabled: true,
    pending: false,
    message: FILE_AUTH_READY,
    expiresAt,
  };
}

export function fileAuthenticationFailed(message) {
  return {
    chooseEnabled: true,
    selectEnabled: false,
    pending: false,
    message: message || FILE_AUTH_DENIED,
    expiresAt: 0,
  };
}

export function fileSelectionClick(state, now = Date.now()) {
  if (!state?.selectEnabled || state.pending) {
    return { openPicker: false, state: state || initialFileSelection() };
  }
  if (!state.expiresAt || state.expiresAt <= now) {
    return { openPicker: false, state: fileAuthenticationFailed(FILE_AUTH_EXPIRED) };
  }
  return { openPicker: true, state };
}

export function keepFileSelection(state) {
  return state || initialFileSelection();
}

export function resetFileSelectionAuthorization() {
  resetListeners.forEach((listener) => listener());
}

export function subscribeFileSelectionReset(listener) {
  resetListeners.add(listener);
  return () => {
    resetListeners.delete(listener);
  };
}
