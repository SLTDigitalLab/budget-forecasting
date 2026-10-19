import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  ALL_CATEGORIES_ID,
  ALL_CATEGORIES_LABEL,
  forecastCategoryOptionLabel,
  isAllCategories,
  isForecastableCategory,
  isInternationalSettlementCategory,
  requestCategoryValue,
  withAllCategoriesOption,
} from "./forecastCategory.js";

const controlsSource = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../components/ForecastControls.jsx"),
  "utf8"
);

test("All Categories is the first dynamic option", () => {
  const options = withAllCategoriesOption([
    { id: "staff_cost", name: "Staff cost", budget_code_count: 65 },
    { name: "Vehicle Cost", budget_code_count: 17 },
  ]);
  assert.equal(options[0].id, ALL_CATEGORIES_ID);
  assert.equal(options[0].name, ALL_CATEGORIES_LABEL);
  assert.equal(options.length, 3);
  assert.equal(requestCategoryValue(options[0]), "all");
  assert.equal(isAllCategories(options[0]), true);
});

test("every discovered category is forecastable, including All Categories", () => {
  assert.equal(isForecastableCategory({ id: ALL_CATEGORIES_ID, name: ALL_CATEGORIES_LABEL }), true);
  assert.equal(isForecastableCategory({ id: "staff_cost", name: "Staff cost" }), true);
  assert.equal(isForecastableCategory({ id: "vehicle_cost", name: "Vehicle Cost" }), true);
  assert.equal(isForecastableCategory({ id: "utility", name: "Utility" }), true);
  assert.equal(isForecastableCategory({ id: "international_settlement", name: "Int'l Settlement" }), true);
  assert.equal(forecastCategoryOptionLabel({ name: "Staff cost" }), "Staff cost");
});

test("International Settlement remains recognizable but is not the only enabled category", () => {
  assert.equal(
    isInternationalSettlementCategory({
      id: "international_settlement",
      name: "International Settlement",
    }),
    true
  );
  assert.equal(isForecastableCategory({ id: "staff_cost", name: "Staff cost" }), true);
  assert.doesNotMatch(forecastCategoryOptionLabel({ name: "Staff cost" }), /Forecast unavailable/i);
});

test("Generate Forecast loads categories dynamically instead of locking International Settlement", () => {
  assert.match(controlsSource, /getForecastAccounts/);
  assert.match(controlsSource, /withAllCategoriesOption/);
  assert.match(controlsSource, /All Categories|ALL_CATEGORIES/);
  assert.doesNotMatch(controlsSource, /Forecast unavailable/);
  assert.doesNotMatch(controlsSource, /disabled=\{!isForecastableCategory/);
  assert.doesNotMatch(controlsSource, /only.*International Settlement/i);
});
