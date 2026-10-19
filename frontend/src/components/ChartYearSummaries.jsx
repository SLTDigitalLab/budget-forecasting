import { formatAmount } from "../utils/forecastDisplay";

export default function ChartYearSummaries({ summaries, amountUnit }) {
  if (!Array.isArray(summaries) || !summaries.length) {
    return null;
  }

  return (
    <div className="analytics-ytd-summary-row" aria-label="Year totals">
      {summaries.map((summary) => (
        <div
          key={summary.id || `${summary.seriesKind}-${summary.title}`}
          className={`analytics-ytd-summary is-${summary.seriesKind === "forecast" ? "forecast" : "actual"}`}
          aria-label={summary.title}
        >
          <div className="analytics-ytd-summary-title">{summary.title}</div>
          <div className="analytics-ytd-summary-amount">
            {summary.total == null ? "Unavailable" : formatAmount(summary.total, amountUnit)}
          </div>
          {summary.coverageLabel ? (
            <div className="analytics-ytd-summary-coverage muted">{summary.coverageLabel}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
