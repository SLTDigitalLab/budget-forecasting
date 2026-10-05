/** Start a Settings action from the click that opened the Microsoft popup. */

export function runGuardedAction(state, acquire, execute) {
  if (state.pending) {
    return Promise.resolve({ started: false });
  }
  state.pending = true;
  let request;
  try {
    request = Promise.resolve(acquire());
  } catch (error) {
    state.pending = false;
    return Promise.reject(error);
  }
  return request
    .then((token) => execute(token))
    .then((value) => ({ started: true, value }))
    .finally(() => {
      state.pending = false;
    });
}

export function isAuthorizationCancellation(error) {
  return /cancelled/i.test(String(error?.message || ""));
}

export function authorizedFileSelection(openPicker) {
  if (typeof openPicker === "function") {
    openPicker();
  }
  return { fallbackRequired: true };
}
