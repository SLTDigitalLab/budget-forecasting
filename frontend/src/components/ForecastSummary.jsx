import { useState } from "react";
import { ArrowDown, ArrowUp, CalendarRange, Minus, TrendingDown, TrendingUp, Wallet } from "lucide-react";
import { buildForecastSummary } from "../utils/forecastSummary";
import { buildForecastCoverage } from "../utils/forecastCoverage";
import { formatAmount, formatPercentChange } from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";

function amountClass(value) {
  if (!Number.isFinite(value) || value === 0) {
    return "";
  }
  return value > 0 ? "is-positive" : "is-negative";
}

function trendClass(direction) {
  if (direction === "up") {
    return "is-positive";
  }
  if (direction === "down") {
    return "is-negative";
  }
  return "";
}

function TrendIcon({ direction }) {
  if (direction === "up") {
    return <TrendingUp size={16} />;
  }
  if (direction === "down") {
    return <TrendingDown size={16} />;
  }
  return <Minus size={16} />;
}

function reasonText(account) {
  const code = String(account?.reason_code || account?.reason || "");
  if (code === "BOOK_ENTRY_ACCOUNT") {
    return "Book Entry Account";
  }
  if (code === "INSUFFICIENT_HISTORICAL_DATA") {
    return "Insufficient Historical Data";
  }
  if (code === "NO_VALID_MODEL") {
    return "No Valid Model";
  }
  return account?.reason_label || account?.reason || "Forecast unavailable";
}

export default function ForecastSummary({ forecast }) {
  const summary = buildForecastSummary(forecast);
  const [excludedOpen, setExcludedOpen] = useState(true);
  if (!summary) {
    return null;
  }
  const unit = forecast?.amount_unit || "LKR";
  const showGrowth = Number.isFinite(summary.historical_average);
  const coverage = buildForecastCoverage(forecast);
  const hasForecast = (forecast?.monthly_forecasts || []).some((row) => Number.isFinite(Number(row?.forecast_amount)));
  const excludedCount = coverage
    ? coverage.bookEntry + coverage.insufficient + coverage.other
    : 0;
  const accounts = coverage?.accounts || [];
  const direction = summary.forecast_trend?.direction;

  return (
    <section className="card section-card forecast-summary-report generate-result-card" aria-labelledby="forecast-summary-heading">
      <div className="section-card-header forecast-summary-heading">
        <h2 id="forecast-summary-heading">Forecast Summary</h2>
        <p>{summary.period}</p>
      </div>
      <div className="section-card-body">
        {hasForecast ? (
        <>
        <section className="forecast-kpi-section" aria-labelledby="forecast-overview-heading">
          <h3 id="forecast-overview-heading">Forecast Overview</h3>
          <ul className="forecast-overview-grid" aria-label="Forecast overview">
            <li className="forecast-kpi-card">
              <span className="forecast-kpi-icon" aria-hidden="true"><Wallet size={16} /></span>
              <span className="forecast-kpi-label">Forecasted Total</span>
              <strong className={amountClass(summary.overall_total)}>{formatAmount(summary.overall_total, unit)}</strong>
            </li>
            <li className="forecast-kpi-card">
              <span className="forecast-kpi-icon" aria-hidden="true"><CalendarRange size={16} /></span>
              <span className="forecast-kpi-label">Monthly Average</span>
              <strong className={amountClass(summary.monthly_average)}>{formatAmount(summary.monthly_average, unit)}</strong>
            </li>
            <li className="forecast-kpi-card">
              <span className="forecast-kpi-icon" aria-hidden="true"><ArrowUp size={16} /></span>
              <span className="forecast-kpi-label">Highest Forecast Month</span>
              <strong className={amountClass(summary.maximum_monthly_forecast)}>{formatAmount(summary.maximum_monthly_forecast, unit)}</strong>
              {summary.peak_month ? <span className="forecast-kpi-sub">{isoToLabel(summary.peak_month)}</span> : null}
            </li>
            <li className="forecast-kpi-card">
              <span className="forecast-kpi-icon" aria-hidden="true"><ArrowDown size={16} /></span>
              <span className="forecast-kpi-label">Lowest Forecast Month</span>
              <strong className={amountClass(summary.minimum_monthly_forecast)}>{formatAmount(summary.minimum_monthly_forecast, unit)}</strong>
              {summary.trough_month ? <span className="forecast-kpi-sub">{isoToLabel(summary.trough_month)}</span> : null}
            </li>
          </ul>
        </section>

        {showGrowth ? (
          <section className="forecast-kpi-section" aria-labelledby="forecast-analysis-heading">
            <h3 id="forecast-analysis-heading">Expenditure Analysis</h3>
            <ul className="forecast-analysis-grid" aria-label="Expenditure analysis">
              <li className="forecast-kpi-card">
                <span className="forecast-kpi-label">Historical Monthly Average</span>
                <strong>{formatAmount(summary.historical_average, unit)}</strong>
              </li>
              <li className="forecast-kpi-card">
                <span className="forecast-kpi-label">Change vs Historical Average</span>
                <strong className={amountClass(summary.difference)}>
                  {formatAmount(summary.difference, unit)}
                  {Number.isFinite(summary.difference_percent)
                    ? ` (${formatPercentChange(summary.difference_percent)})`
                    : ""}
                </strong>
              </li>
              <li className="forecast-kpi-card">
                <span className="forecast-kpi-icon" aria-hidden="true"><TrendIcon direction={direction} /></span>
                <span className="forecast-kpi-label">Forecast Trend</span>
                <strong className={trendClass(direction)}>{summary.forecast_trend?.label || "—"}</strong>
              </li>
            </ul>
          </section>
        ) : null}
        </>
        ) : (
          <p className="muted">No forecast available for the selected category.</p>
        )}

        {coverage ? (
          <div className="forecast-coverage-layout">
            <section aria-labelledby="forecast-coverage-heading">
              <h3 id="forecast-coverage-heading">Forecast Coverage</h3>
              <table className="forecast-coverage-table">
                <thead>
                  <tr>
                    <th>Description</th>
                    <th>Budget Codes</th>
                  </tr>
                </thead>
                <tbody>
                  {coverage.rows.map(([label, value]) => (
                    <tr key={label}>
                      <td>{label}</td>
                      <td>{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section aria-labelledby="forecast-excluded-heading">
              <button
                type="button"
                className="forecast-excluded-toggle"
                aria-expanded={excludedOpen}
                aria-controls="forecast-excluded-panel"
                onClick={() => setExcludedOpen((open) => !open)}
              >
                <h3 id="forecast-excluded-heading">
                  Excluded & Unavailable Budget Codes
                  <span>{excludedCount}</span>
                </h3>
              </button>
              {excludedOpen ? (
                <div id="forecast-excluded-panel">
                  {accounts.length ? (
                    <div className="forecast-excluded-scroll">
                      <table className="forecast-excluded-table">
                        <thead>
                          <tr>
                            <th>Budget Code</th>
                            <th>Budget Code Name</th>
                            <th>Category</th>
                            <th>Status</th>
                            <th>Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {accounts.map((account) => (
                            <tr key={`${account.budget_code || account.account_code}-${reasonText(account)}`}>
                              <td>{account.budget_code || account.account_code}</td>
                              <td>{account.budget_name || account.account_name || "—"}</td>
                              <td>{account.category || "—"}</td>
                              <td><span className="forecast-status-badge">UNAVAILABLE</span></td>
                              <td>{reasonText(account)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : excludedCount > 0 ? (
                    <p className="muted">
                      {excludedCount} selected budget codes are excluded or unavailable.
                    </p>
                  ) : (
                    <p className="forecast-coverage-success">All selected budget codes were forecasted.</p>
                  )}
                </div>
              ) : null}
            </section>
          </div>
        ) : null}
      </div>
    </section>
  );
}
