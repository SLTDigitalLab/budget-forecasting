import logo from "../assets/slt-mobitel-logo.svg";
import AnalyticsSectionHeading from "./AnalyticsSectionHeading";
import ExpectedRangeLabel from "./ExpectedRangeLabel";
import MonthlyForecastChart from "./MonthlyForecastChart";
import { formatAmount, formatPercentChange } from "../utils/forecastDisplay";
import { UNAVAILABLE, isFiniteBound } from "../utils/forecastReport";
import { isoToLabel } from "../utils/months";

function growthClass(value) {
  if (!Number.isFinite(value) || value === 0) {
    return "";
  }
  return value > 0 ? "analytics-growth-up" : "analytics-growth-down";
}

function formatGeneratedDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "—";
  }
  return date.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function boundLabel(value, amountUnit) {
  if (value === UNAVAILABLE || !isFiniteBound(value)) {
    return UNAVAILABLE;
  }
  return formatAmount(value, amountUnit);
}

export default function ForecastReportPreview({ report }) {
  if (!report) {
    return null;
  }
  const unit = report.amount_unit;
  const summary = report.summary;
  const hasBounds = (report.chart_months || []).some(
    (row) => isFiniteBound(row.lower_bound) && isFiniteBound(row.upper_bound)
  );
  const chartData = (report.chart_months || []).map((row) => ({
    month: isoToLabel(row.month),
    amount: row.forecast_amount,
    lower: isFiniteBound(row.lower_bound) ? Number(row.lower_bound) : null,
    upper: isFiniteBound(row.upper_bound) ? Number(row.upper_bound) : null,
    intervalStatus: row.interval_status,
  }));

  return (
    <article className="card export-report" aria-label="Forecast report preview">
      <header className="export-report-header">
        <img className="export-report-logo" src={logo} alt="SLT-Mobitel" />
        <div>
          <p className="export-report-kicker">SLT-Mobitel</p>
          <h2>{report.title}</h2>
        </div>
      </header>
      <section className="export-report-section" aria-labelledby="export-header-heading">
        <AnalyticsSectionHeading id="export-header-heading" index="01" title="Report header" />
        <dl className="export-report-meta">
        <div>
          <dt>Forecast period</dt>
          <dd>{report.header.period}</dd>
        </div>
        <div>
          <dt>Generated date</dt>
          <dd>{formatGeneratedDate(report.header.generated_date)}</dd>
        </div>
        <div>
          <dt>Category</dt>
          <dd>{report.header.category}</dd>
        </div>
        <div>
          <dt>Forecast months</dt>
          <dd>{report.header.forecast_month_count}</dd>
        </div>
        <div>
          <dt>Selected Budget Codes</dt>
          <dd>{report.header.selected_account_count}</dd>
        </div>
        </dl>
      </section>

      <section className="export-report-section" aria-labelledby="export-summary-heading">
        <AnalyticsSectionHeading id="export-summary-heading" index="02" title="Forecast summary" />
        <ul className="analytics-kpis">
          <li>
            <span>Forecasted total</span>
            <strong>{formatAmount(summary.overall_total, unit)}</strong>
          </li>
          <li>
            <span>Monthly average</span>
            <strong>{formatAmount(summary.monthly_average, unit)}</strong>
          </li>
          <li>
            <span>Highest forecast month</span>
            <strong>
              {formatAmount(summary.maximum_monthly_forecast, unit)}
              {summary.peak_month ? <em>{isoToLabel(summary.peak_month)}</em> : null}
            </strong>
          </li>
          <li>
            <span>Lowest forecast month</span>
            <strong>
              {formatAmount(summary.minimum_monthly_forecast, unit)}
              {summary.trough_month ? <em>{isoToLabel(summary.trough_month)}</em> : null}
            </strong>
          </li>
          <li>
            <span>Historical monthly baseline</span>
            <strong>{formatAmount(summary.historical_average, unit)}</strong>
          </li>
          <li>
            <span>Difference from history</span>
            <strong className={growthClass(summary.difference)}>
              {formatAmount(summary.difference, unit)}
            </strong>
          </li>
          <li>
            <span>Percentage change from history</span>
            <strong className={growthClass(summary.difference_percent)}>
              {formatPercentChange(summary.difference_percent)}
            </strong>
          </li>
          <li>
            <span>Predicted direction</span>
            <strong>{summary.forecast_trend?.label || "—"}</strong>
          </li>
        </ul>
      </section>

      <section className="export-report-section" aria-labelledby="export-insights-heading">
        <AnalyticsSectionHeading id="export-insights-heading" index="03" title="Key financial insights" />
        {report.insights.length ? (
          <ul className="forecast-summary-insights">
            {report.insights.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">No financial insights are available for this forecast.</p>
        )}
      </section>

      <section className="export-report-section" aria-labelledby="export-chart-heading">
        <AnalyticsSectionHeading id="export-chart-heading" index="04" title="Monthly forecast visualization" />
        {hasBounds ? <ExpectedRangeLabel /> : null}
        <ul className="export-chart-legend">
          <li>
            <span className="export-legend-swatch is-forecast" />
            Forecast
          </li>
          {hasBounds ? (
            <li>
              <span className="export-legend-swatch is-range" />
              Expected Range (90%)
            </li>
          ) : null}
        </ul>
        <div className="analytics-chart analytics-chart-wide">
          <MonthlyForecastChart data={chartData} amountUnit={unit} />
        </div>
      </section>

      <section className="export-report-section" aria-labelledby="export-codes-heading">
        <AnalyticsSectionHeading id="export-codes-heading" index="05" title="Budget Code contribution" />
        <div className="table-wrap data-scroll data-scroll-lg">
          <table>
            <thead>
              <tr>
                <th>Rank</th>
                <th>Budget Code</th>
                <th>Account name</th>
                <th>Forecast total</th>
                <th>Percentage contribution</th>
              </tr>
            </thead>
            <tbody>
              {report.budget_codes.map((row) => (
                <tr key={row.budget_code}>
                  <td>{row.rank}</td>
                  <td>{row.budget_code}</td>
                  <td>{row.account_name || "—"}</td>
                  <td>{formatAmount(row.forecast_amount, unit)}</td>
                  <td>{`${(Number(row.percent) * 100).toFixed(1)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="export-report-section" aria-labelledby="export-monthly-heading">
        <AnalyticsSectionHeading id="export-monthly-heading" index="06" title="Monthly forecast table" />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Month</th>
                <th>Forecast amount</th>
                <th>Lower expected value</th>
                <th>Upper expected value</th>
                <th>Change from previous month</th>
              </tr>
            </thead>
            <tbody>
              {report.monthly_forecasts.map((row) => (
                <tr key={row.month}>
                  <td>{isoToLabel(row.month)}</td>
                  <td>{formatAmount(row.forecast_amount, unit)}</td>
                  <td>{boundLabel(row.lower_bound, unit)}</td>
                  <td>{boundLabel(row.upper_bound, unit)}</td>
                  <td className={growthClass(row.change_percent)}>
                    {formatPercentChange(row.change_percent)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="export-report-section" aria-labelledby="export-comparison-heading">
        <AnalyticsSectionHeading id="export-comparison-heading" index="07" title="Historical vs forecast comparison" />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Forecast month</th>
                <th>Historical average</th>
                <th>Forecast amount</th>
                <th>Difference</th>
                <th>Percentage change</th>
                <th>Historical periods used</th>
              </tr>
            </thead>
            <tbody>
              {report.comparison.map((row) => (
                <tr key={row.month}>
                  <td>{isoToLabel(row.month)}</td>
                  <td>{formatAmount(row.historical_average, unit)}</td>
                  <td>{formatAmount(row.forecast_amount, unit)}</td>
                  <td className={growthClass(row.difference)}>{formatAmount(row.difference, unit)}</td>
                  <td className={growthClass(row.difference_percent)}>
                    {formatPercentChange(row.difference_percent)}
                  </td>
                  <td>
                    {row.historical_periods_used?.length
                      ? row.historical_periods_used
                          .map((item) => `${isoToLabel(item.month)} (${formatAmount(item.actual, unit)})`)
                          .join("; ")
                      : UNAVAILABLE}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  );
}
