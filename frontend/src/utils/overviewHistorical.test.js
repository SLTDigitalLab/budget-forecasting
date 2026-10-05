import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  OVERVIEW_PARTIAL_NOTE,
  compactOverviewError,
  overviewHistoricalNote,
} from "./overviewHistorical.js";

const overviewSource = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../pages/Overview.jsx"),
  "utf8"
);

test("a production-code mismatch is not shown as a giant Budget Code list", () => {
  const raw = `Production Budget Codes are missing from the historical Actuals workbook: ${Array.from({ length: 80 }, (_, index) => `C${index}`).join(", ")}`;
  assert.equal(compactOverviewError(raw), OVERVIEW_PARTIAL_NOTE);
  assert.doesNotMatch(compactOverviewError(raw), /C12/);
});

test("partial historical coverage uses a compact non-blocking note", () => {
  assert.equal(
    overviewHistoricalNote({ historical_data_partial: true, historical_missing_budget_code_count: 3 }),
    OVERVIEW_PARTIAL_NOTE
  );
  assert.equal(overviewHistoricalNote({ historical_data_partial: false, historical_missing_budget_code_count: 0 }), "");
});

test("Overview page sanitizes the old mismatch error and does not render hundreds of IDs", () => {
  assert.match(overviewSource, /compactOverviewError/);
  assert.match(overviewSource, /overviewHistoricalNote/);
  assert.doesNotMatch(overviewSource, /historical_missing_budget_codes\.map/);
});
