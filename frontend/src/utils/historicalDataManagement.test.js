import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { formatHistoryPeriod } from "./historyPeriod.js";
import { activityActorFromUser } from "../auth/microsoftLogin.js";
import { confirmationBlocked, displayAmount, duplicateRecordCount } from "./historicalWorkflow.js";

const here = dirname(fileURLToPath(import.meta.url));
const settings = readFileSync(resolve(here, "../pages/Settings.jsx"), "utf8");
const panels = readFileSync(resolve(here, "../components/HistoricalDataPanels.jsx"), "utf8");
const datasetApi = readFileSync(resolve(here, "../api/datasetApi.js"), "utf8");
const appSource = readFileSync(resolve(here, "../App.jsx"), "utf8");
const envExample = readFileSync(resolve(here, "../../../.env.example"), "utf8");
const styles = readFileSync(resolve(here, "../styles/global.css"), "utf8");
const mainSource = readFileSync(resolve(here, "../main.jsx"), "utf8");

test("Settings hosts Historical Data Management", () => {
  assert.match(appSource, /path="settings" element=\{<GuardedPage><Settings \/><\/GuardedPage>\}/);
  assert.match(settings, /Manage historical financial data\./);
  assert.doesNotMatch(settings, /does not retrain/);
  assert.match(settings, /Validate & Preview/);
  assert.match(panels, /Confirm & Save/);
  assert.match(settings, /Revision History/);
  assert.match(panels, /View Master Dataset/);
  assert.match(panels, /Edit Master Dataset/);
  assert.match(settings, /Rollback/);
  assert.match(settings, /Choose File/);
  assert.match(settings, /History Period/);
  assert.match(settings, />Name</);
  assert.match(settings, /Uploaded By/);
  assert.match(settings, /Last Edited By/);
  assert.doesNotMatch(settings, /<ErrorState/);
  assert.doesNotMatch(settings, /msalApiToken/);
  assert.doesNotMatch(settings, /VITE_AZURE_API_SCOPE/);
  assert.doesNotMatch(settings, /type="email"/);
  assert.doesNotMatch(settings, /placeholder=.*[Ee]mail/);
});

test("Settings summary combines the history period and hides failed counts", () => {
  assert.equal(formatHistoryPeriod("2023-01", "2026-06"), "2023-01 – 2026-06");
  assert.equal(formatHistoryPeriod("2024-03", "2024-03"), "2024-03");
  assert.equal(formatHistoryPeriod("", ""), "—");
  assert.match(settings, /showSummaryValues/);
  assert.match(settings, /showSummaryEmpty/);
  assert.match(settings, /loadFailed/);
  assert.match(settings, /No historical actuals are stored yet/);
  assert.match(settings, /production_workbook/);
  assert.match(settings, /data_source_label/);
  assert.match(settings, /Data Source/);
  assert.match(settings, /Data Source/);
  assert.match(panels, /Confirm & Save/);
  assert.match(panels, /Save Changes/);
  assert.match(panels, /Confirm Changes/);
  assert.match(panels, /Review Rollback/);
  assert.match(panels, /Confirm Rollback/);
  assert.match(panels, /LATER REVISION CONFLICT/);
  assert.match(panels, /Replace with missing/);
  assert.match(styles, /padding: 24px/);
});

test("dataset reads stay open and changes send the selected API token", () => {
  assert.doesNotMatch(datasetApi, /currentActivity\(\)/);
  assert.match(datasetApi, /preview_id: previewId/);
  assert.match(datasetApi, /Authorization: `Bearer \$\{options\.token\}`/);
  assert.match(datasetApi, /return datasetRequest\("\/api\/datasets\/summary"\)/);
  assert.doesNotMatch(datasetApi, /department/);
  assert.doesNotMatch(datasetApi, /jobTitle/);
  assert.doesNotMatch(datasetApi, /getApiAccessToken/);
  assert.doesNotMatch(datasetApi, /uploaded_by/);
  assert.doesNotMatch(datasetApi, /edited_by/);
  assert.doesNotMatch(mainSource, /initializeApiAuth/);
  assert.doesNotMatch(envExample, /AZURE_APPROVED_OBJECT_IDS/);
});

test("staged confirmation stays blocked until validation passes", () => {
  assert.equal(confirmationBlocked({ can_confirm: false }), true);
  assert.equal(confirmationBlocked({ can_confirm: true }), false);
  assert.equal(displayAmount(null), "-");
  assert.equal(displayAmount(0), 0);
  assert.equal(duplicateRecordCount({ duplicate_conflicts: [{ records: [{}, {}] }] }), 2);
  assert.match(panels, /Edit/);
  assert.match(panels, /Remove/);
});

test("activity metadata uses the Microsoft login profile without inventing email", () => {
  assert.deepEqual(activityActorFromUser({ name: "Ada Lovelace", email: "ada@example.com" }), {
    name: "Ada Lovelace",
    email: "ada@example.com",
  });
  assert.deepEqual(activityActorFromUser({ name: "Ada Lovelace" }), { name: "Ada Lovelace", email: "" });
  assert.deepEqual(activityActorFromUser(null), { name: "", email: "" });
});
