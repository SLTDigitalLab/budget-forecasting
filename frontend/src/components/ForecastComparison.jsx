import { useEffect, useMemo, useRef, useState } from "react";
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
import BoundedChartTooltip from "./BoundedChartTooltip";
import EmptyState from "./EmptyState";
import { comparisonTotals } from "../utils/forecastComparison";
import useChartPalette from "../hooks/useChartPalette";
import { cartesianChartMargin, SOLID_BAR_RADIUS, valueYAxisProps } from "../utils/chartLayout";
import {
  axisUnitLabel,
  formatAmount,
  formatAxisTick,
  formatPercentChange,
  periodLabel,
} from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";

function monthTitle(isoMonth) {
  return isoToLabel(isoMonth || "").replace("-", " ");
}

function ChartTooltip({ children }) {
  return <div className="chart-tooltip chart-tooltip-overlay">{children}</div>;
}

export default function ForecastComparison({
  rows,
  amountUnit,
  forecast = null,
  historyStart = null,
  historyEnd = null,
  coverageNote = "",
}) {
  const palette = useChartPalette();
  const hostRef = useRef(null);
  const [active, setActive] = useState(null);
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
          title="Same-month historical average vs forecast"
        />
        <p className="muted">
          Each forecast month is compared with the monthly average of the same calendar month in prior actual years.
          This is a monthly-average comparison, not annual year-over-year growth.
        </p>
      </div>

      <section className="analytics-meta-grid" aria-label="Comparison context">
        <div className="analytics-meta-item">
          <span>Forecast category</span>
          <strong>{forecast?.category || "—"}</strong>
        </div>
        <div className="analytics-meta-item">
          <span>Actual historical period used</span>
          <strong>
            {historyStart && historyEnd
              ? `${monthTitle(historyStart)} – ${monthTitle(historyEnd)}`
              : "—"}
          </strong>
        </div>
        <div className="analytics-meta-item">
          <span>Forecast period</span>
          <strong>{periodLabel(forecast) || "—"}</strong>
        </div>
      </section>
      {coverageNote ? <p className="analytics-partial-note">{coverageNote}</p> : null}

      <ul className="analytics-kpis" aria-label="Comparison snapshot">
        <li>
          <span>Forecast total</span>
          <strong>{formatAmount(totals.forecast_total, amountUnit)}</strong>
          <em className="analytics-kpi-note">Sum of forecast months only</em>
        </li>
        <li>
          <span>Same-month historical monthly averages (sum)</span>
          <strong>{formatAmount(totals.historical_total, amountUnit)}</strong>
          <em className="analytics-kpi-note">Not a full-year actual total</em>
        </li>
        <li>
          <span>Monthly-average difference</span>
          <strong className="analytics-comparison-delta">
            {formatAmount(totals.difference, amountUnit)}
          </strong>
          <em className="analytics-kpi-note">Informational; higher spend is not scored as better</em>
        </li>
        <li>
          <span>Relative change</span>
          <strong className="analytics-comparison-delta">
            {formatPercentChange(totals.difference_percent)}
          </strong>
        </li>
      </ul>

      <article className="card analytics-main">
        <div className="analytics-main-head">
          <div>
            <h3>Monthly-average comparison by forecast month</h3>
            <p className="muted">
              Historical bars are the average of matching calendar months in prior actual years for the active forecast category.
            </p>
          </div>
        </div>
        <div
          className="analytics-chart analytics-chart-wide"
          role="img"
          aria-label="Historical monthly average versus forecast"
          ref={hostRef}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartRows}
              margin={cartesianChartMargin({ bottom: 16 })}
              barGap={4}
              barCategoryGap="18%"
              onMouseMove={(state) => {
                if (!state?.isTooltipActive || !state.activePayload?.length || !state.activeCoordinate) {
                  setActive(null);
                  return;
                }
                setActive({
                  point: state.activePayload[0].payload,
                  x: state.activeCoordinate.x,
                  y: state.activeCoordinate.y,
                });
              }}
              onMouseLeave={() => setActive(null)}
            >
              <CartesianGrid stroke={palette.grid} vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: palette.tick }} tickMargin={8} minTickGap={18} interval="preserveStartEnd" />
              <YAxis {...valueYAxisProps(unit, { axisFill: palette.axis })} tickFormatter={formatAxisTick} />
              <Tooltip cursor={{ fill: palette.cursor }} content={() => null} wrapperStyle={{ display: "none" }} />
              <Legend wrapperStyle={{ fontSize: 12, color: palette.legend }} />
              <Bar dataKey="historical_average" name="Historical monthly average" fill={palette.historical} radius={SOLID_BAR_RADIUS} maxBarSize={22} isAnimationActive={false} />
              <Bar
                dataKey="forecast_amount"
                name="Forecast"
                fill={palette.forecast}
                radius={SOLID_BAR_RADIUS}
                maxBarSize={22}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
          <BoundedChartTooltip active={Boolean(active)} anchor={active} hostRef={hostRef}>
            {active ? (
              <ChartTooltip>
                <div className="chart-tooltip-month">{monthTitle(active.point.month)}</div>
                <div className="chart-tooltip-value">
                  {`Historical monthly average ${active.point.historical_average == null ? "—" : formatAmount(active.point.historical_average, amountUnit)}`}
                </div>
                <div className="chart-tooltip-value">
                  {`Forecast ${formatAmount(active.point.forecast_amount, amountUnit)}`}
                </div>
                <div className="chart-tooltip-series">
                  {`${active.point.observation_count} historical month${active.point.observation_count === 1 ? "" : "s"} used`}
                </div>
              </ChartTooltip>
            ) : null}
          </BoundedChartTooltip>
        </div>
      </article>

      <div className="analytics-comparison-layout">
        <article className="card table-card">
          <h3>Monthly comparison</h3>
          <p className="muted">Select a forecast month to inspect the historical periods used in the monthly average.</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Forecast month</th>
                  <th>Historical monthly average</th>
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
                    <td className="analytics-comparison-delta">
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
              <p className="muted">Historical periods and monthly-average comparison for the selected forecast month.</p>
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
                {selected.historical_periods_used?.length ? (
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
                <h4 id="analytics-month-metrics-heading">Calculated monthly-average comparison</h4>
                <dl className="analytics-month-detail-metrics-list">
                  <div>
                    <dt>Historical monthly average</dt>
                    <dd>{formatAmount(selected.historical_average, amountUnit)}</dd>
                  </div>
                  <div>
                    <dt>Forecast</dt>
                    <dd>{formatAmount(selected.forecast_amount, amountUnit)}</dd>
                  </div>
                  <div>
                    <dt>Difference</dt>
                    <dd className="analytics-comparison-delta">
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
