import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  ACTION_RESUME_KEY,
  cancelSettingsAction,
  claimReturnedActionToken,
  consumeSettingsActionResume,
  rememberReturnedActionToken,
  requestSettingsAction,
  saveActionResume,
  startSettingsActionRedirect,
  subscribeActionPrompt,
} from "../auth/actionAuthentication.js";
import { authorizedFileSelection, isAuthorizationCancellation, runGuardedAction } from "./settingsActions.js";
import {
  FILE_AUTH_CANCELLED,
  FILE_AUTH_DENIED,
  FILE_AUTH_EXPIRED,
  FILE_AUTH_READY,
  beginFileAuthentication,
  fileAuthenticationFailed,
  fileAuthenticationSucceeded,
  fileSelectionClick,
  initialFileSelection,
  keepFileSelection,
  resetFileSelectionAuthorization,
  subscribeFileSelectionReset,
} from "./fileSelectionAuth.js";

const here = dirname(fileURLToPath(import.meta.url));
const settings = readFileSync(resolve(here, "../pages/Settings.jsx"), "utf8");
const panels = readFileSync(resolve(here, "../components/HistoricalDataPanels.jsx"), "utf8");
const experience = readFileSync(resolve(here, "../components/RetrainExperience.jsx"), "utf8");

test("each protected click starts account selection and cancellation performs no action", async () => {
  const state = { pending: false };
  let executed = false;
  let popups = 0;
  await assert.rejects(
    () => runGuardedAction(state, () => {
      popups += 1;
      throw new Error("Microsoft sign-in was cancelled.");
    }, async () => {
      executed = true;
    }),
    /cancelled/i,
  );
  assert.equal(executed, false);
  assert.equal(popups, 1);
  assert.equal(state.pending, false);
  assert.equal(isAuthorizationCancellation(new Error("Microsoft sign-in was cancelled.")), true);
});

test("a second click while authentication is pending does not start another action", async () => {
  const state = { pending: false };
  let popups = 0;
  let runs = 0;
  let release;
  const first = runGuardedAction(state, () => {
    popups += 1;
    return new Promise((resolve) => {
      release = resolve;
    });
  }, async () => {
    runs += 1;
  });
  const second = await runGuardedAction(state, () => {
    popups += 1;
    return "other-token";
  }, async () => {
    runs += 1;
  });
  assert.equal(second.started, false);
  assert.equal(popups, 1);
  release("selected-token");
  const completed = await first;
  assert.equal(completed.started, true);
  assert.equal(runs, 1);
});

test("the popup opens synchronously before the action promise continues", () => {
  const state = { pending: false };
  const order = [];
  const pending = runGuardedAction(state, () => {
    order.push("popup");
    return Promise.resolve("selected-token");
  }, async (token) => {
    order.push(token);
  });
  assert.deepEqual(order, ["popup"]);
  return pending.then(() => {
    assert.deepEqual(order, ["popup", "selected-token"]);
  });
});

test("upload file selection authorizes once, then opens the picker only from Select File", () => {
  const initial = initialFileSelection();
  assert.equal(initial.chooseEnabled, true);
  assert.equal(initial.selectEnabled, false);

  const pending = beginFileAuthentication(initial);
  assert.equal(pending.started, true);
  assert.equal(pending.state.pending, true);
  assert.equal(pending.state.chooseEnabled, false);
  assert.equal(pending.state.selectEnabled, false);
  assert.equal(beginFileAuthentication(pending.state).started, false);
  assert.equal(fileSelectionClick(pending.state).openPicker, false);

  const granted = fileAuthenticationSucceeded(Date.now() + 60_000, Date.now());
  assert.equal(granted.chooseEnabled, false);
  assert.equal(granted.selectEnabled, true);
  assert.equal(granted.message, FILE_AUTH_READY);
  const firstPick = fileSelectionClick(granted, Date.now());
  assert.equal(firstPick.openPicker, true);
  const afterCancel = keepFileSelection(firstPick.state);
  assert.equal(afterCancel.selectEnabled, true);
  const secondPick = fileSelectionClick(afterCancel, Date.now());
  assert.equal(secondPick.openPicker, true);
  assert.equal(secondPick.state.selectEnabled, true);

  const cancelled = fileAuthenticationFailed(FILE_AUTH_CANCELLED);
  assert.equal(cancelled.chooseEnabled, true);
  assert.equal(cancelled.selectEnabled, false);
  assert.equal(cancelled.message, FILE_AUTH_CANCELLED);
  const denied = fileAuthenticationFailed(FILE_AUTH_DENIED);
  assert.equal(denied.selectEnabled, false);
  assert.equal(denied.message, FILE_AUTH_DENIED);

  const expired = fileSelectionClick({ ...granted, expiresAt: Date.now() - 1000 }, Date.now());
  assert.equal(expired.openPicker, false);
  assert.equal(expired.state.chooseEnabled, true);
  assert.equal(expired.state.selectEnabled, false);
  assert.equal(expired.state.message, FILE_AUTH_EXPIRED);
  assert.equal(fileAuthenticationSucceeded(0, Date.now()).message, FILE_AUTH_EXPIRED);

  let resets = 0;
  const stop = subscribeFileSelectionReset(() => {
    resets += 1;
  });
  resetFileSelectionAuthorization();
  assert.equal(resets, 1);
  stop();
  resetFileSelectionAuthorization();
  assert.equal(resets, 1);

  assert.match(settings, /disabled=\{!fileAuth\.chooseEnabled/);
  assert.match(settings, /disabled=\{!fileAuth\.selectEnabled/);
  assert.equal(FILE_AUTH_READY, "Authorized — select a file to continue");
  assert.match(settings, /fileAuth\.message/);
  assert.match(settings, /if \(!next\)/);
  assert.match(settings, /function handlePreview/);
  const preview = settings.slice(settings.indexOf("function handlePreview"), settings.indexOf("function chooseFile"));
  assert.match(preview, /protect\("preview-dataset"/);
  const selectFile = settings.slice(settings.indexOf("function selectAuthorizedFile"), settings.indexOf("async function handleSave"));
  assert.match(selectFile, /\.click\(/);
  assert.doesNotMatch(selectFile, /protect\(|assertEditorAccess/);
  assert.doesNotMatch(settings, /sessionStorage\.setItem\([^)]*file/);
  const authContext = readFileSync(resolve(here, "../auth/AuthContext.jsx"), "utf8");
  assert.match(authContext, /resetFileSelectionAuthorization/);
  const styles = readFileSync(resolve(here, "../styles/global.css"), "utf8");
  assert.match(styles, /\.settings-upload-button:disabled/);
  assert.match(styles, /border-radius: 8px/);
  assert.match(styles, /#023E8A/);
});

test("file selection stays closed until authorization and then offers Select File", () => {
  let opened = false;
  const blocked = authorizedFileSelection(() => {
    opened = true;
    return false;
  });
  assert.equal(opened, true);
  assert.equal(blocked.fallbackRequired, true);
  assert.match(settings, /onClick=\{chooseFile\}/);
  assert.match(settings, /Select File/);
  assert.match(settings, /selectAuthorizedFile/);
  assert.doesNotMatch(settings, /htmlFor="dataset-file"/);
  const selectFile = settings.slice(settings.indexOf("function selectAuthorizedFile"), settings.indexOf("async function handleSave"));
  assert.doesNotMatch(selectFile, /acquireActionAccessToken|assertEditorAccess/);
});

test("protected settings actions show the in-app card before any Microsoft redirect", () => {
  assert.match(settings, /requestSettingsAction/);
  assert.doesNotMatch(settings, /acquireActionAccessToken|window\.open/);
  assert.match(experience, /requestSettingsAction/);
  assert.doesNotMatch(experience, /acquireActionAccessToken|window\.open/);
  const chooseFile = settings.slice(settings.indexOf("function chooseFile"), settings.indexOf("function selectAuthorizedFile"));
  assert.doesNotMatch(chooseFile, /\.click\(/);
  assert.match(settings, /Please authenticate to make changes|requestSettingsAction/);
});

test("sign-in redirects in this tab and resume state does not contain the access token", () => {
  const session = memoryStore();
  const local = memoryStore();
  let href = "";
  const url = startSettingsActionRedirect({
    descriptor: { kind: "begin-retrain", destructive: true, payload: { accessToken: "must-not-be-copied" } },
    env: {
      VITE_AZURE_TENANT_ID: "11111111-1111-1111-1111-111111111111",
      VITE_AZURE_CLIENT_ID: "22222222-2222-2222-2222-222222222222",
    },
    location: {
      origin: "http://localhost",
      pathname: "/settings",
      set href(value) { href = value; },
    },
    sessionStore: session,
    localStore: local,
  });
  assert.match(url, /prompt=select_account/);
  assert.match(url, /scope=User\.Read/);
  assert.equal(href, url);
  const saved = JSON.parse(session.getItem(ACTION_RESUME_KEY));
  assert.equal(saved.kind, "begin-retrain");
  assert.equal(saved.destructive, true);
  assert.equal(Object.hasOwn(saved, "accessToken"), false);
  assert.doesNotMatch(session.getItem(ACTION_RESUME_KEY), /eyJ|secret/);
});

test("a refresh without a new token does not repeat a destructive action", () => {
  const session = memoryStore();
  saveActionResume({ kind: "begin-retrain", destructive: true, payload: {} }, session);
  const refreshed = consumeSettingsActionResume(session);
  assert.equal(refreshed.mode, "restore");
  assert.equal(session.getItem(ACTION_RESUME_KEY), null);
  rememberReturnedActionToken("graph-token");
  saveActionResume({ kind: "confirm-dataset", destructive: true, payload: { previewId: "p1" } }, session);
  const resumed = consumeSettingsActionResume(session);
  assert.equal(resumed.mode, "run");
  assert.equal(claimReturnedActionToken(), "graph-token");
  assert.equal(consumeSettingsActionResume(session), null);
  assert.equal(claimReturnedActionToken(), "");
});

test("cancel closes the card and performs no action", () => {
  const session = memoryStore();
  let shown = "unset";
  const stop = subscribeActionPrompt((prompt) => {
    shown = prompt;
  });
  requestSettingsAction({ kind: "choose-file", payload: {} });
  assert.equal(shown.kind, "choose-file");
  cancelSettingsAction(session);
  assert.equal(shown, null);
  assert.equal(session.getItem(ACTION_RESUME_KEY), null);
  stop();
});

function memoryStore(initial = {}) {
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
  };
}

test("read actions do not request another popup and mutations do", () => {
  const openView = settings.slice(settings.indexOf("async function openView"), settings.indexOf("async function refreshMaster"));
  assert.doesNotMatch(openView, /acquireActionAccessToken|assertEditorAccess/);
  assert.match(settings, /onRetry=\{load\}/);
  assert.match(settings, /function startMasterEdit/);
  assert.match(settings, /function openRollback/);
  assert.match(settings, /function askRetrain/);
  assert.match(settings, /assertEditorAccess/);
  assert.match(panels, /onAuthorize/);
  assert.match(panels, /View Master Dataset/);
  assert.match(experience, /Retry training/);
  assert.match(experience, /setDismissedJobId\(status\.job_id\)/);
  assert.match(experience, /cancelRetrain/);
  const closeButton = experience.slice(experience.indexOf("setDismissedJobId(status.job_id)"), experience.indexOf("setDismissedJobId(status.job_id)") + 40);
  assert.doesNotMatch(closeButton, /runAction/);
});