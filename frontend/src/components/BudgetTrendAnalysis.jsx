import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AnalyticsSectionHeading from "./AnalyticsSectionHeading";
import EmptyState from "./EmptyState";
import ExpectedRangeLabel, {
  EXPECTED_RANGE_LABEL,
  EXPECTED_RANGE_UNAVAILABLE,
} from "./ExpectedRangeLabel";
import { cartesianChartMargin, valueYAxisProps } from "../utils/chartLayout";
import {
  axisUnitLabel,
  formatAmount,
  formatAxisTick,
  formatPercentChange,
} from "../utils/forecastDisplay";

function ChartTooltip({ children }) {
  return <div className="chart-tooltip">{children}</div>;
}

function trendClass(direction) {
  if (direction === "up") {
    return "analytics-growth-up";
  }
  if (direction === "down") {
    return "analytics-growth-down";
  }
  return "";
}

function changeClass(value) {
  if (!Number.isFinite(value) || value === 0) {
    return "";
  }
  return value > 0 ? "analytics-growth-up" : "analytics-growth-down";
}

export default function BudgetTrendAnalysis({ trend, amountUnit }) {
  const unit = axisUnitLabel(amountUnit);
  const points = trend?.points || [];
  const showRange = Boolean(trend?.has_expected_range);

  if (!trend?.forecast_month_count) {
    return (
      <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-trend-heading">
        <AnalyticsSectionHeading id="analytics-trend-heading" index="02" title="Budget Trend Analysis" />
        <article className="card empty-panel analytics-forecast-optional">
          <EmptyState message="Budget Trend Analysis is available after a forecast is generated." />
        </article>
      </section>
    );
  }

  return (
    <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-trend-heading">
      <div>
        <AnalyticsSectionHeading id="analytics-trend-heading" index="02" title="Budget Trend Analysis" />
        <p className="muted">
          Historical expenditure and predicted budget on one timeline. The two series do not overlap.
        </p>
      </div>

      <ul className="analytics-kpis" aria-label="Budget trend snapshot">
        <li>
          <span>Historical average</span>
          <strong>{formatAmount(trend.historical_average, amountUnit)}</strong>
        </li>
        <li>
          <span>Predicted average</span>
          <strong>{formatAmount(trend.forecast_average, amountUnit)}</strong>
        </li>
        <li>
          <span>Change vs history</span>
          <strong className={changeClass(trend.difference)}>
            {Number.isFinite(trend.difference)
              ? `${formatAmount(trend.difference, amountUnit)} (${formatPercentChange(trend.difference_percent)})`
              : "—"}
          </strong>
        </li>
        <li>
          <span>Trend</span>
          <strong>
            <em className={trendClass(trend.historical_trend?.direction)}>{trend.historical_trend?.label || "—"}</em>
            {" / "}
            <em className={trendClass(trend.forecast_trend?.direction)}>{trend.forecast_trend?.label || "—"}</em>
          </strong>
        </li>
      </ul>

      <article className="card analytics-main">
        <div className="analytics-main-head">
          <div>
            <h3>Historical and predicted trend</h3>
            <p className="muted">
              Stored actuals continue through history end. Predicted months use the generated forecast.
            </p>
            {showRange ? <ExpectedRangeLabel /> : null}
          </div>
        </div>
        {!points.length ? (
          <EmptyState message="No historical actuals or forecast months are available for trend analysis." />
        ) : (
          <div className="analytics-chart analytics-chart-wide" role="img" aria-label="Budget Trend Analysis">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={points} margin={cartesianChartMargin()}>
                <defs>
                  <linearGradient id="budgetTrendRangeFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#1f7ae0" stopOpacity={0.18} />
                    <stop offset="95%" stopColor="#1f7ae0" stopOpacity={0.04} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#e6eef6" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 10 }} minTickGap={22} tickMargin={6} />
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
                        <div className="chart-tooltip-month">{point.label}</div>
                        {point.actual != null ? (
                          <div className="chart-tooltip-value">{`Historical ${formatAmount(point.actual, amountUnit)}`}</div>
                        ) : null}
                        {point.forecast != null ? (
                          <div className="chart-tooltip-value">{`Predicted ${formatAmount(point.forecast, amountUnit)}`}</div>
                        ) : null}
                        {Number.isFinite(Number(point.lower)) && Number.isFinite(Number(point.upper)) ? (
                          <div className="chart-tooltip-series">
                            {`${EXPECTED_RANGE_LABEL} ${formatAmount(point.lower, amountUnit)} – ${formatAmount(point.upper, amountUnit)}`}
                          </div>
                        ) : point.interval_status === "unavailable_beyond_calibrated_horizon" ? (
                          <div className="chart-tooltip-series">{EXPECTED_RANGE_UNAVAILABLE}</div>
                        ) : null}
                      </ChartTooltip>
                    );
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {showRange ? (
                  <Area
                    type="linear"
                    dataKey="upper"
                    name="Upper expected range"
                    stroke="none"
                    fill="url(#budgetTrendRangeFill)"
                    legendType="none"
                    isAnimationActive={false}
                    connectNulls={false}
                  />
                ) : null}
                {showRange ? (
                  <Area
                    type="linear"
                    dataKey="lower"
                    name="Lower expected range"
                    stroke="none"
                    fill="#ffffff"
                    fillOpacity={1}
                    legendType="none"
                    isAnimationActive={false}
                    connectNulls={false}
                  />
                ) : null}
                <Line
                  type="monotone"
                  dataKey="actual"
                  name="Historical expenditure"
                  stroke="#071a33"
                  strokeWidth={2.2}
                  dot={false}
                  connectNulls={false}
                />
                <Line
                  type="monotone"
                  dataKey="forecast"
                  name="Predicted budget"
                  stroke="#1f7ae0"
                  strokeWidth={2.4}
                  dot={false}
                  connectNulls={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </article>
    </section>
  );
}
