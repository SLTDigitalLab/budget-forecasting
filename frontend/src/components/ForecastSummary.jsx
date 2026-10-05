import { buildForecastSummary } from "../utils/forecastSummary";
import { buildForecastCoverage } from "../utils/forecastCoverage";
import { formatAmount, formatPercentChange } from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";

function growthClass(value) {
  if (!Number.isFinite(value) || value === 0) {
    return "";
  }
  return value > 0 ? "analytics-growth-up" : "analytics-growth-down";
}

function trendChangeValue(direction) {
  if (direction === "up") {
    return 1;
  }
  if (direction === "down") {
    return -1;
  }
  return 0;
}

export default function ForecastSummary({ forecast }) {
  const summary = buildForecastSummary(forecast);
  if (!summary) {
    return null;
  }
  const unit = forecast?.amount_unit || "LKR";
  const showGrowth = Number.isFinite(summary.historical_average);
  const coverage = buildForecastCoverage(forecast);

  return (
    <section className="card section-card forecast-summary-report generate-result-card" aria-labelledby="forecast-summary-heading">
      <h2 id="forecast-summary-heading" className="section-card-header">Forecast Summary</h2>
      <div className="section-card-body">
      <p className="muted">
        Predicted budgets and expenditure growth for {summary.period}.
      </p>
      {coverage ? (
        <p className="forecast-coverage-note" aria-live="polite">
          {coverage.message}
          {coverage.detail ? ` ${coverage.detail}` : ""}
        </p>
      ) : null}

      <h3>Predicted budgets</h3>
      <ul className="analytics-kpis" aria-label="Predicted budgets">
        <li>
          <span>Forecasted total</span>
          <strong>{formatAmount(summary.overall_total, unit)}</strong>
        </li>
        <li>
          <span>Monthly average</span>
          <strong>{formatAmount(summary.monthly_average, unit)}</strong>
        </li>
        <li>
          <span>Lowest month</span>
          <strong>
            {formatAmount(summary.minimum_monthly_forecast, unit)}
            {summary.trough_month ? <em>{isoToLabel(summary.trough_month)}</em> : null}
          </strong>
        </li>
        <li>
          <span>Highest month</span>
          <strong>
            {formatAmount(summary.maximum_monthly_forecast, unit)}
            {summary.peak_month ? <em>{isoToLabel(summary.peak_month)}</em> : null}
          </strong>
        </li>
      </ul>

      {showGrowth ? (
        <>
          <h3>Expenditure growth</h3>
          <ul className="analytics-kpis" aria-label="Expenditure growth">
            <li>
              <span>Historical monthly average</span>
              <strong>{formatAmount(summary.historical_average, unit)}</strong>
            </li>
            <li>
              <span>Change vs history</span>
              <strong className={growthClass(summary.difference)}>
                {formatAmount(summary.difference, unit)}
                {Number.isFinite(summary.difference_percent)
                  ? ` (${formatPercentChange(summary.difference_percent)})`
                  : ""}
              </strong>
            </li>
            <li>
              <span>Predicted direction</span>
              <strong className={growthClass(trendChangeValue(summary.forecast_trend?.direction))}>
                {summary.forecast_trend?.label || "—"}
              </strong>
            </li>
            <li>
              <span>Predicted months</span>
              <strong>{summary.month_count}</strong>
            </li>
          </ul>
        </>
      ) : null}
      </div>
    </section>
  );
}
