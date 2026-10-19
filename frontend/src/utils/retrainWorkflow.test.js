import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { acquireActionAccessToken, runAuthorizedAction } from "../auth/apiAccessToken.js";
import {
  CONFIRM_TEXT,
  LOCK_MESSAGE,
  READY_TEXT,
  SAVED_TEXT,
  backdropDismisses,
  blocksNavigation,
  countdownSeconds,
  escapeDismisses,
  failedResponseError,
  modalDismissible,
  progressText,
  restoreRetrainPresentation,
  shouldStickToBottom,
  showSaveModels,
} from "./retrainWorkflow.js";

const here = dirname(fileURLToPath(import.meta.url));
const settings = readFileSync(resolve(here, "../pages/Settings.jsx"), "utf8");
const experience = readFileSync(resolve(here, "../components/RetrainExperience.jsx"), "utf8");
const layout = readFileSync(resolve(here, "../components/AppLayout.jsx"), "utf8");
const forecastApi = readFileSync(resolve(here, "../api/forecastApi.js"), "utf8");
const datasetApi = readFileSync(resolve(here, "../api/datasetApi.js"), "utf8");
const retrainApi = readFileSync(resolve(here, "../api/retrainApi.js"), "utf8");
const styles = readFileSync(resolve(here, "../styles/global.css"), "utf8");

test("Settings asks for confirmation before retraining", () => {
  const master = settings.indexOf("<h2>Master Dataset</h2>");
  const retrain = settings.indexOf("<h2>Retrain Models</h2>");
  const revision = settings.indexOf("<h2>Revision History</h2>");
  assert.ok(master > 0 && master < retrain && retrain < revision);
  assert.match(settings, /retrain-card/);
  assert.match(settings, /Data updated — retraining required/);
  assert.match(settings, /Last successful model publication time is unavailable/);
  assert.match(settings, /requestSettingsAction/);
  assert.doesNotMatch(settings, /acquireActionAccessToken/);
  assert.doesNotMatch(settings, /rememberRetrainSession/);
  assert.match(settings, /CONFIRM_TEXT/);
  assert.equal(CONFIRM_TEXT, "The system will be unavailable until training and model saving finish.");
  assert.match(styles, /min\(90vw, 1400px\)/);
  assert.match(styles, /85vh/);
  assert.match(styles, /#023E8A/);
});

test("active and ready-to-save modals stay open", () => {
  for (const state of ["QUEUED", "TRAINING", "VALIDATING", "READY_TO_SAVE", "SAVING"]) {
    assert.equal(modalDismissible(state), false);
    assert.equal(escapeDismisses(state), false);
    assert.equal(blocksNavigation(state), true);
  }
  assert.equal(backdropDismisses(), false);
  assert.equal(showSaveModels("READY_TO_SAVE"), true);
  assert.equal(showSaveModels("TRAINING"), false);
  assert.match(experience, /escapeDismisses/);
  assert.match(experience, /data-backdrop-dismiss/);
  assert.match(experience, /Save Models/);
  assert.match(experience, /READY_TEXT/);
  assert.match(layout, /RetrainExperience/);
});

test("progress uses real counts and does not invent a percentage", () => {
  assert.equal(progressText({ progress_stage: "final_training", progress_current: 3, progress_total: 10 }), "Final training 3 of 10");
  assert.equal(progressText({ progress_stage: "evaluation", progress_current: null, progress_total: null }), "Evaluating models");
  assert.equal(progressText({ progress_stage: "evaluation" }).includes("%"), false);
});

test("a refresh restores the manager modal and other users see the lock", () => {
  assert.doesNotMatch(experience, /rememberRetrainSession/);
  assert.doesNotMatch(experience, /X-Retrain-Token/);
  assert.match(experience, /requestSettingsAction/);
  assert.doesNotMatch(experience, /acquireActionAccessToken/);
  assert.match(experience, /shouldStickToBottom/);
  assert.match(retrainApi, /Authorization: `Bearer \$\{token\}`/);
  assert.doesNotMatch(retrainApi, /X-Retrain-Token/);
  assert.doesNotMatch(datasetApi, /department/);
  assert.doesNotMatch(datasetApi, /jobTitle/);
  assert.equal(shouldStickToBottom(1000, 900, 80), true);
  assert.equal(shouldStickToBottom(1000, 400, 80), false);
  const ready = {
    locked: true,
    can_manage: true,
    job_id: "job-1",
    state: "READY_TO_SAVE",
    logs: "[Final Training 1/2] 511101",
  };
  assert.deepEqual(restoreRetrainPresentation(ready, "", Date.now()), { mode: "modal", dismissible: false });
  assert.equal(restoreRetrainPresentation({ ...ready, can_manage: false }, "", Date.now()).mode, "modal");
  assert.equal(LOCK_MESSAGE, "Model training in progress. Please wait.");
});

test("the success countdown closes from the backend deadline", () => {
  const serverNow = "2026-10-02T00:00:00.000Z";
  const status = {
    locked: true,
    can_manage: true,
    job_id: "job-1",
    state: "SAVED",
    server_now: serverNow,
    unlock_at: "2026-10-02T00:00:05.000Z",
  };
  const start = Date.parse(serverNow);
  assert.equal(countdownSeconds(status, start, start), 5);
  assert.equal(countdownSeconds(status, start + 5000, start), 0);
  assert.equal(restoreRetrainPresentation(status, "", start, start).mode, "modal");
  assert.equal(restoreRetrainPresentation(status, "", start + 5000, start).mode, "hidden");
  assert.equal(SAVED_TEXT, "Models saved successfully");
  assert.equal(READY_TEXT, "Training completed — ready to save");
  assert.equal(
    progressText({ progress_stage: "final_training", progress_current: 3, progress_total: 10, progress_budget_code: "511101" }),
    "Final training 3 of 10 · 511101",
  );
});

test("a locked response is recognized without trusting a display name", () => {
  const error = failedResponseError(423, {
    detail: LOCK_MESSAGE,
    code: "SYSTEM_LOCKED",
  });
  assert.equal(error.status, 423);
  assert.equal(error.code, "SYSTEM_LOCKED");
  assert.equal(error.message, LOCK_MESSAGE);
  assert.match(forecastApi, /failedResponseError/);
  assert.match(datasetApi, /failedResponseError/);
  assert.match(datasetApi, /return datasetRequest\("\/api\/datasets\/summary"\)/);
});

test("cancelling account selection performs no mutation and the selected token is used", async () => {
  let called = false;
  await assert.rejects(
    () => runAuthorizedAction(async () => {
      called = true;
    }, async () => {
      throw new Error("Microsoft sign-in was cancelled.");
    }),
    /cancelled/i,
  );
  assert.equal(called, false);
  const listeners = [];
  const pending = acquireActionAccessToken({
    env: {
      VITE_AZURE_TENANT_ID: "11111111-1111-1111-1111-111111111111",
      VITE_AZURE_CLIENT_ID: "22222222-2222-2222-2222-222222222222",
    },
    location: { origin: "http://localhost:5174" },
    storage: { setItem() {}, removeItem() {} },
    openWindow(url) {
      assert.match(url, /prompt=select_account/);
      assert.match(url, /scope=User\.Read/);
      assert.doesNotMatch(url, /access_as_user/);
      return { closed: false };
    },
    listen: {
      addEventListener(_type, fn) { listeners.push(fn); },
      removeEventListener() {},
      setTimeout() { return 1; },
      clearTimeout() {},
      setInterval() { return 1; },
      clearInterval() {},
    },
  });
  listeners[0]({ origin: "http://localhost:5174", data: { type: "retrain-api-token", accessToken: "selected-account-token" } });
  let used = "";
  const token = await pending;
  await runAuthorizedAction(async (value) => {
    used = value;
  }, async () => token);
  assert.equal(used, "selected-account-token");
});
