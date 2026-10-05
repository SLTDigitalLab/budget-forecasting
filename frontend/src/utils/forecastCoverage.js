export function buildForecastCoverage(forecast) {
  const coverage = forecast?.forecast_coverage && typeof forecast.forecast_coverage === "object"
    ? forecast.forecast_coverage
    : {};
  const total = Number(
    coverage.total_budget_codes
    ?? coverage.requested_account_count
    ?? forecast?.selected_account_count
    ?? 0
  );
  const forecasted = Number(
    coverage.forecasted_budget_codes
    ?? coverage.included_count
    ?? 0
  );
  const unavailable = Number(
    coverage.unavailable_budget_codes
    ?? coverage.unavailable_count
    ?? 0
  );
  const zeroPolicy = Number(coverage.zero_policy_budget_codes ?? 0);
  const partial = Boolean(
    forecast?.partial_forecast
    ?? coverage.partial_forecast
    ?? coverage.partial_total
  );
  if (!Number.isFinite(total) || total < 1) {
    return null;
  }
  const covered = Number.isFinite(forecasted) && forecasted > 0 ? forecasted : Math.max(0, total - (Number.isFinite(unavailable) ? unavailable : 0));
  const lines = [`Forecast coverage: ${covered} / ${total} Budget Codes`];
  if (partial && unavailable > 0) {
    lines.push(
      unavailable === 1
        ? "1 Budget Code unavailable due to insufficient historical data."
        : `${unavailable} Budget Codes unavailable due to insufficient historical data.`
    );
  }
  return {
    total,
    forecasted: covered,
    unavailable: Number.isFinite(unavailable) ? unavailable : 0,
    zeroPolicy: Number.isFinite(zeroPolicy) ? zeroPolicy : 0,
    partial,
    message: lines[0],
    detail: lines[1] || "",
    lines,
  };
}
