import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AnalyticsSectionHeading from "./AnalyticsSectionHeading";
import EmptyState from "./EmptyState";
import { comparisonTotals } from "../utils/forecastComparison";
import { cartesianChartMargin, valueYAxisProps } from "../utils/chartLayout";
import {
  axisUnitLabel,
  formatAmount,
  formatAxisTick,
  formatPercentChange,
} from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";

function monthTitle(isoMonth) {
  return isoToLabel(isoMonth || "").replace("-", " ");
}

function ChartTooltip({ children }) {
  return <div className="chart-tooltip">{children}</div>;
}

function differenceClass(value) {
  if (!Number.isFinite(value) || value === 0) {
    return "";
  }
  return value > 0 ? "analytics-growth-up" : "analytics-growth-down";
}

export default function ForecastComparison({ rows, amountUnit }) {
  const [selectedMonth, setSelectedMonth] = useState(rows[0]?.month || "");
  const totals = useMemo(() => comparisonTotals(rows), [rows]);
  const selected = rows.find((row) => row.month === selectedMonth) || rows[0] || null;
  const unit = axisUnitLabel(amountUnit);
  const chartRows = rows.map((row) => ({
    ...row,
    label: isoToLabel(row.month),
  }));

  useEffect(() => {
    if (!rows.some((row) => row.month === selectedMonth)) {
      setSelectedMonth(rows[0]?.month || "");
    }
  }, [rows, selectedMonth]);

  if (!rows.length) {
    return (
      <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-comparison-heading">
        <AnalyticsSectionHeading
          id="analytics-comparison-heading"
          index="03"
          title="Historical vs Forecast Comparison"
        />
        <article className="card empty-panel">
          <EmptyState message="No forecast months are available to compare with historical actuals." />
        </article>
      </section>
    );
  }

  return (
    <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-comparison-heading">
      <div>
        <AnalyticsSectionHeading
          id="analytics-comparison-heading"
          index="03"
          title="Historical vs Forecast Comparison"
        />
        <p className="muted">
          Each forecast month is compared with the same-calendar-month historical average.
          The exact historical months used in that average are listed for full transparency.
        </p>
      </div>

      <ul className="analytics-kpis" aria-label="Comparison snapshot">
        <li>
          <span>Forecast total</span>
          <strong>{formatAmount(totals.forecast_total, amountUnit)}</strong>
        </li>
        <li>
          <span>Historical baseline</span>
          <strong>{formatAmount(totals.historical_total, amountUnit)}</strong>
        </li>
        <li>
          <span>Difference</span>
          <strong className={differenceClass(totals.difference)}>
            {formatAmount(totals.difference, amountUnit)}
          </strong>
        </li>
        <li>
          <span>Change</span>
          <strong className={differenceClass(totals.difference_percent)}>
            {formatPercentChange(totals.difference_percent)}
          </strong>
        </li>
      </ul>

      <article className="card analytics-main">
        <div className="analytics-main-head">
          <div>
            <h3>Historical average vs forecast</h3>
            <p className="muted">
              Historical bars are the average of the same calendar month in prior actual years, not the forecast month itself.
            </p>
          </div>
        </div>
        <div className="analytics-chart analytics-chart-wide" role="img" aria-label="Historical average versus forecast">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartRows} margin={cartesianChartMargin()}>
              <CartesianGrid stroke="#e6eef6" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} tickMargin={6} />
              <YAxis {...valueYAxisProps(unit)} tickFormatter={formatAxisTick} />
              <Tooltip
                allowEscapeViewBox={{ x: true, y: true }}
                wrapperStyle={{ zIndex: 5, pointerEvents: "none" }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) {
                    return null;
                  }
                  const point = payload[0].payload;
                  return (
                    <ChartTooltip>
                      <div className="chart-tooltip-month">{monthTitle(point.month)}</div>
                      <div className="chart-tooltip-value">
                        {`Historical average ${point.historical_average == null ? "—" : formatAmount(point.historical_average, amountUnit)}`}
                      </div>
                      <div className="chart-tooltip-value">
                        {`Forecast ${formatAmount(point.forecast_amount, amountUnit)}`}
                      </div>
                      <div className="chart-tooltip-series">
                        {`${point.observation_count} historical month${point.observation_count === 1 ? "" : "s"} used`}
                      </div>
                    </ChartTooltip>
                  );
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="historical_average" name="Historical average" fill="#071a33" radius={[4, 4, 0, 0]} maxBarSize={22} />
              <Bar dataKey="forecast_amount" name="Forecast" fill="#1f7ae0" radius={[4, 4, 0, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </article>

      <div className="analytics-comparison-layout">
        <article className="card table-card">
          <h3>Monthly comparison</h3>
          <p className="muted">Select a forecast month to inspect the historical periods used.</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Forecast month</th>
                  <th>Historical average</th>
                  <th>Forecast</th>
                  <th>Difference</th>
                  <th>Periods used</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.month}
                    className={selected?.month === row.month ? "active" : ""}
                    onClick={() => setSelectedMonth(row.month)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedMonth(row.month);
                      }
                    }}
                    tabIndex={0}
                    aria-selected={selected?.month === row.month}
                  >
                    <td>{monthTitle(row.month)}</td>
                    <td>{formatAmount(row.historical_average, amountUnit)}</td>
                    <td>{formatAmount(row.forecast_amount, amountUnit)}</td>
                    <td className={differenceClass(row.difference)}>
                      {`${formatAmount(row.difference, amountUnit)} (${formatPercentChange(row.difference_percent)})`}
                    </td>
                    <td>{row.observation_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        {selected ? (
          <article className="card analytics-month-detail">
            <header className="analytics-month-detail-head">
              <h3>Selected month details</h3>
              <p className="muted">Historical periods and comparison for the selected forecast month.</p>
              <p className="analytics-month-detail-date">{monthTitle(selected.month)}</p>
            </header>

            <div className="analytics-month-detail-body">
              <section className="analytics-month-detail-section" aria-labelledby="analytics-month-periods-heading">
                <div className="analytics-month-detail-section-head">
                  <h4 id="analytics-month-periods-heading">Historical periods used</h4>
                  <span>
                    {selected.observation_count === 1
                      ? "1 period"
                      : `${selected.observation_count} periods`}
                  </span>
                </div>
                {selected.historical_periods_used.length ? (
                  <ul className="analytics-month-detail-periods">
                    {selected.historical_periods_used.map((period) => (
                      <li key={period.month}>
                        <span>{monthTitle(period.month)}</span>
                        <strong>{formatAmount(period.actual, amountUnit)}</strong>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted analytics-month-detail-empty">
                    No prior same-calendar-month actuals are available for this forecast month.
                  </p>
                )}
              </section>

              <section className="analytics-month-detail-section analytics-month-detail-metrics" aria-labelledby="analytics-month-metrics-heading">
                <h4 id="analytics-month-metrics-heading">Calculated comparison</h4>
                <dl className="analytics-month-detail-metrics-list">
                  <div>
                    <dt>Historical average</dt>
                    <dd>{formatAmount(selected.historical_average, amountUnit)}</dd>
                  </div>
                  <div>
                    <dt>Forecast</dt>
                    <dd>{formatAmount(selected.forecast_amount, amountUnit)}</dd>
                  </div>
                  <div>
                    <dt>Difference</dt>
                    <dd className={differenceClass(selected.difference)}>
                      <span>{formatAmount(selected.difference, amountUnit)}</span>
                      <em>{`(${formatPercentChange(selected.difference_percent)})`}</em>
                    </dd>
                  </div>
                </dl>
              </section>
            </div>
          </article>
        ) : null}
      </div>
    </section>
  );
}
