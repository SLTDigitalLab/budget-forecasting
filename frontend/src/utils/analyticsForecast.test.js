import assert from "node:assert/strict";
import test from "node:test";
import { resolveAnalyticsForecast } from "./analyticsForecast.js";

const sampleForecast = {
  requested_start_month: "2026-07",
  requested_end_month: "2026-12",
  monthly_forecasts: [{ month: "2026-07", forecast_amount: 10 }],
  overall_total: 10,
};

test("current in-memory forecast is used without fetching history", async () => {
  let fetched = false;
  const result = await resolveAnalyticsForecast({
    currentForecast: sampleForecast,
    fetchLatest: async () => {
      fetched = true;
      return { overall_total: 99 };
    },
  });
  assert.equal(result.status, "memory");
  assert.equal(result.forecast, sampleForecast);
  assert.equal(fetched, false);
});

test("latest completed database forecast is restored when memory is empty", async () => {
  const stored = { ...sampleForecast, overall_total: 42 };
  const result = await resolveAnalyticsForecast({
    currentForecast: null,
    fetchLatest: async () => stored,
  });
  assert.equal(result.status, "history");
  assert.equal(result.forecast, stored);
});

test("refresh restores Analytics from the latest completed forecast", async () => {
  const stored = { ...sampleForecast, generated_at: "2026-08-25T10:00:00Z" };
  const result = await resolveAnalyticsForecast({
    currentForecast: null,
    fetchLatest: async () => stored,
  });
  assert.equal(result.status, "history");
  assert.equal(result.forecast.generated_at, "2026-08-25T10:00:00Z");
});

test("empty state appears only when no completed forecast exists", async () => {
  const result = await resolveAnalyticsForecast({
    currentForecast: null,
    fetchLatest: async () => null,
  });
  assert.equal(result.status, "empty");
  assert.equal(result.forecast, null);
});

test("backend failure displays an error instead of the empty state", async () => {
  await assert.rejects(
    () => resolveAnalyticsForecast({
      currentForecast: null,
      fetchLatest: async () => {
        throw new Error("Unable to connect to the forecast database.");
      },
    }),
    (error) => {
      assert.equal(error.message, "Unable to connect to the forecast database.");
      return true;
    }
  );
});
