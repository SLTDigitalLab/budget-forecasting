import { useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ErrorBar,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AnalyticsSectionHeading from "./AnalyticsSectionHeading";
import BoundedChartTooltip from "./BoundedChartTooltip";
import ChartYearSummaries from "./ChartYearSummaries";
import EmptyState from "./EmptyState";
import ExpectedRangeLabel, {
  EXPECTED_RANGE_LABEL,
  EXPECTED_RANGE_UNAVAILABLE,
} from "./ExpectedRangeLabel";
import useChartPalette from "../hooks/useChartPalette";
import {
  cartesianChartMargin,
  SOLID_BAR_RADIUS,
  valueYAxisProps,
  withIntervalError,
} from "../utils/chartLayout";
import {
  axisUnitLabel,
  formatAmount,
  formatAxisTick,
  formatPercentChange,
} from "../utils/forecastDisplay";
import {
  filterRecentHistoryWithForecast,
  isYearTotalRow,
  summarizeMixedDisplayedYears,
} from "../utils/chartYearTotals";

function ChartTooltip({ children }) {
  return <div className="chart-tooltip chart-tooltip-overlay">{children}</div>;
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

function TrendTooltipBody({ point, amountUnit }) {
  return (
    <ChartTooltip>
      <div className="chart-tooltip-month">{point.label}</div>
      {point.actual != null ? (
        <div className="chart-tooltip-value">{`Historical ${formatAmount(point.actual, amountUnit)}`}</div>
      ) : null}
      {point.forecast != null ? (
        <div className="chart-tooltip-value">{`Predicted ${formatAmount(point.forecast, amountUnit)}`}</div>
      ) : null}
      {point.forecast != null && Number.isFinite(Number(point.lower)) && Number.isFinite(Number(point.upper)) ? (
        <div className="chart-tooltip-series">
          {`${EXPECTED_RANGE_LABEL} ${formatAmount(point.lower, amountUnit)} – ${formatAmount(point.upper, amountUnit)}`}
        </div>
      ) : point.forecast != null && point.interval_status === "unavailable_beyond_calibrated_horizon" ? (
        <div className="chart-tooltip-series">{EXPECTED_RANGE_UNAVAILABLE}</div>
      ) : null}
    </ChartTooltip>
  );
}

export default function BudgetTrendAnalysis({ trend, amountUnit }) {
  const palette = useChartPalette();
  const unit = axisUnitLabel(amountUnit);
  const [historyView, setHistoryView] = useState("recent2");
  const containerRef = useRef(null);
  const [active, setActive] = useState(null);

  const windowedPoints = useMemo(() => {
    const points = (trend?.points || []).filter((row) => !isYearTotalRow(row));
    if (historyView === "full") {
      return points;
    }
    return filterRecentHistoryWithForecast(points, { isoKey: "month" });
  }, [trend?.points, historyView]);

  const yearSummaries = useMemo(
    () => summarizeMixedDisplayedYears(windowedPoints, { isoKey: "month" }),
    [windowedPoints]
  );

  const showRange = Boolean(trend?.has_expected_range);

  if (!trend?.forecast_month_count) {
    return (
      <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-trend-heading">
        <AnalyticsSectionHeading id="analytics-trend-heading" title="Budget Trend Analysis" />
        <article className="card empty-panel analytics-forecast-optional">
          <EmptyState message="Budget Trend Analysis is available after a forecast is generated." />
        </article>
      </section>
    );
  }

  return (
    <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-trend-heading">
      <div>
        <AnalyticsSectionHeading id="analytics-trend-heading" title="Budget Trend Analysis" />
        <p className="muted">
          Historical expenditure and predicted budget on one timeline. The two series do not overlap.
        </p>
      </div>

      <ul className="analytics-kpis" aria-label="Budget trend snapshot">
        <li>
          <span>Historical average</span>
          <strong>{formatAmount(trend.historical_average, amountUnit)}</strong>
          <em className="analytics-kpi-note">Full stored history</em>
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
          <span>Historical trend</span>
          <strong className={trendClass(trend.historical_trend?.direction)}>
            {trend.historical_trend?.label || "—"}
          </strong>
        </li>
        <li>
          <span>Forecast trend</span>
          <strong className={trendClass(trend.forecast_trend?.direction)}>
            {trend.forecast_trend?.label || "—"}
          </strong>
        </li>
      </ul>

      <article className="card analytics-main">
        <div className="analytics-main-head">
          <div>
            <h3>Historical and predicted trend</h3>
            <p className="muted">
              {historyView === "recent2"
                ? "Recent history: 2 full years + available partial year (from latest stored actual), then the selected forecast period."
                : "Showing the full stored actuals history plus the selected forecast period."}
            </p>
            {showRange ? <ExpectedRangeLabel /> : null}
          </div>
          <div className="analytics-history-view" role="group" aria-label="History window">
            <button
              type="button"
              className={historyView === "recent2" ? "active" : ""}
              onClick={() => setHistoryView("recent2")}
            >
              Recent 2 years
            </button>
            <button
              type="button"
              className={historyView === "full" ? "active" : ""}
              onClick={() => setHistoryView("full")}
            >
              Full history
            </button>
          </div>
        </div>
        {!windowedPoints.length ? (
          <EmptyState message="No historical actuals or forecast months are available for trend analysis." />
        ) : (
          <div className="analytics-expenditure-panel">
            <ChartYearSummaries summaries={yearSummaries} amountUnit={amountUnit} />
            <div
              className="analytics-chart analytics-chart-wide analytics-chart-fit"
              role="img"
              aria-label="Budget Trend Analysis"
              ref={containerRef}
            >
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={withIntervalError(windowedPoints, "forecast")}
                  margin={cartesianChartMargin({ bottom: 20, top: 8, right: 12 })}
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
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 10, fill: palette.tick }}
                    minTickGap={22}
                    tickMargin={8}
                    interval="preserveStartEnd"
                  />
                  <YAxis {...valueYAxisProps(unit, { axisFill: palette.axis })} tickFormatter={formatAxisTick} />
                  <Tooltip cursor={{ fill: palette.cursor }} content={() => null} wrapperStyle={{ display: "none" }} />
                  <Legend wrapperStyle={{ fontSize: 12, color: palette.legend }} />
                  <Bar
                    dataKey="actual"
                    name="Historical expenditure"
                    fill={palette.historical}
                    maxBarSize={22}
                    radius={SOLID_BAR_RADIUS}
                    isAnimationActive={false}
                  />
                  <Bar
                    dataKey="forecast"
                    name="Predicted budget"
                    fill={palette.forecast}
                    maxBarSize={22}
                    radius={SOLID_BAR_RADIUS}
                    isAnimationActive={false}
                  >
                    {showRange ? (
                      <ErrorBar dataKey="intervalError" width={6} strokeWidth={1.25} stroke={palette.interval} direction="y" />
                    ) : null}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <BoundedChartTooltip active={Boolean(active)} anchor={active} hostRef={containerRef}>
                {active ? <TrendTooltipBody point={active.point} amountUnit={amountUnit} /> : null}
              </BoundedChartTooltip>
            </div>
          </div>
        )}
      </article>
    </section>
  );
}
