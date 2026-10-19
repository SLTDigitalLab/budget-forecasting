import { formatAmount } from "../utils/forecastDisplay";
import {
  UNAVAILABLE_REASON,
  budgetCodeForecastDisclosure,
  buildBudgetCodeMonthForecast,
} from "../utils/budgetCodeForecast";
import EmptyState from "./EmptyState";
import LoadingState from "./LoadingState";

function boundLabel(value, amountUnit) {
  return Number.isFinite(Number(value)) ? formatAmount(value, amountUnit) : "—";
}

function percentLabel(value) {
  if (!Number.isFinite(Number(value))) {
    return "—";
  }
  return `${Number(value).toFixed(2)}%`;
}

export default function BudgetCodeForecastTable({
  forecast,
  amountUnit = "LKR",
  loading = false,
  expanded = false,
  onToggle,
}) {
  const disclosure = budgetCodeForecastDisclosure({ forecast, expanded });
  if (!disclosure.showAction) {
    return null;
  }
  const result = buildBudgetCodeMonthForecast(forecast);

  return (
    <section className="budget-code-forecast-disclosure">
      <div className="card table-card budget-code-forecast-actions">
        <button
          type="button"
          className="generate-button"
          aria-expanded={disclosure.showTable}
          aria-controls="budget-code-forecast-panel"
          onClick={onToggle}
        >
          {disclosure.actionLabel}
        </button>
      </div>
      {disclosure.showTable ? (
        <section
          id="budget-code-forecast-panel"
          className="card section-card table-card generate-result-card"
          aria-labelledby="budget-code-forecast-heading"
        >
          <h2 id="budget-code-forecast-heading" className="section-card-header">{result.title}</h2>
          <div className="section-card-body">
          {loading ? (
            <LoadingState label="Loading Budget Code-wise forecast..." />
          ) : result.error ? (
            <EmptyState message={result.error} />
          ) : (
            <>
              <p className="sr-only">
                {`Forecast amounts for ${result.rows.length} Budget Codes in ${result.monthLabel}. Combined total ${formatAmount(result.combined_total, amountUnit)}.`}
              </p>
              <div className="table-wrap data-scroll data-scroll-lg">
                <table className="budget-code-forecast-table">
                  <thead>
                    <tr>
                      <th>Rank</th>
                      <th>Budget Code</th>
                      <th>Budget Name</th>
                      <th className="amount">Forecast</th>
                      <th>Model</th>
                      <th>Status</th>
                      <th className="amount">Forecast Contribution to Total (%)</th>
                      <th className="amount">Lower Expected Value</th>
                      <th className="amount">Upper Expected Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row) => (
                      <tr
                        key={row.budget_code}
                        className={row.available === false ? "is-unavailable" : ""}
                        title={row.available === false ? (row.reason || UNAVAILABLE_REASON) : undefined}
                      >
                        <td>{row.rank}</td>
                        <td>{row.budget_code}</td>
                        <td>{row.account_name || "—"}</td>
                        <td className="amount">
                          {row.available === false ? "—" : formatAmount(row.forecast_amount, amountUnit)}
                        </td>
                        <td>{row.algorithm || "—"}</td>
                        <td>{row.display_status || (row.available === false ? "Forecast Unavailable" : "Available")}</td>
                        <td className="amount">{row.available === false ? "—" : percentLabel(row.contribution_percent)}</td>
                        <td className="amount">{boundLabel(row.lower_bound, amountUnit)}</td>
                        <td className="amount">{boundLabel(row.upper_bound, amountUnit)}</td>
                      </tr>
                    ))}
                    <tr className="total-row">
                      <td colSpan={3}>{`Combined ${result.monthLabel} Forecast`}</td>
                      <td className="amount">{formatAmount(result.combined_total, amountUnit)}</td>
                      <td colSpan={2} />
                      <td className="amount">{percentLabel(100)}</td>
                      <td className="amount">{boundLabel(result.combined_lower_bound, amountUnit)}</td>
                      <td className="amount">{boundLabel(result.combined_upper_bound, amountUnit)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </>
          )}
          </div>
        </section>
      ) : null}
    </section>
  );
}
