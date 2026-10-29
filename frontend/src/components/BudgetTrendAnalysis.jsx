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
  filterRecentHistoryWithForecast,
  isYearTotalRow,
  summarizeMixedDisplayedYears,
} from "../utils/chartYearTotals";
import {
  axisUnitLabel,
  formatAmount,
  formatAxisTick,
  formatPercentChange,
} from "../utils/forecastDisplay";
import {
  HISTORY_RANGE_ALL,
  HISTORY_RANGE_LAST_24,
  formatDisplayedRangeLabel,
  resolveHistoricalDisplayWindow,
} from "../utils/historicalDisplayWindow";
import { isoToLabel } from "../utils/months";

function ChartTooltip({ children }) {
  return <div className="chart-tooltip chart-tooltip-overlay">{children}</div>;
}

function monthTitle(isoMonth) {
  return isoToLabel(isoMonth || "").replace("-", " ");
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
      {point.actual == null && point.forecast == null ? (
        <div className="chart-tooltip-series">No actual or forecast for this month</div>
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
  const [historyView, setHistoryView] = useState(HISTORY_RANGE_LAST_24);
  const containerRef = useRef(null);
  const [active, setActive] = useState(null);

  const historyWindow = useMemo(() => {
    if (!trend?.history_end) {
      return null;
    }
    return resolveHistoricalDisplayWindow(
      trend.history_end,
      trend.history_start,
      historyView === HISTORY_RANGE_ALL ? HISTORY_RANGE_ALL : HISTORY_RANGE_LAST_24
    );
  }, [trend?.history_end, trend?.history_start, historyView]);

  const windowedPoints = useMemo(() => {
    const points = (trend?.points || []).filter((row) => !isYearTotalRow(row));
    if (historyView === HISTORY_RANGE_ALL) {
      return points;
    }
    return filterRecentHistoryWithForecast(points, {
      isoKey: "month",
      historyStartIso: trend?.history_start || null,
    });
  }, [trend?.points, trend?.history_start, historyView]);

  const displayedActualRange = useMemo(() => {
    const actualMonths = windowedPoints
      .filter((row) => row.actual != null)
      .map((row) => row.month);
    if (!actualMonths.length) {
      return historyWindow;
    }
    return {
      startIso: actualMonths[0],
      endIso: actualMonths[actualMonths.length - 1],
    };
  }, [windowedPoints, historyWindow]);

  const yearSummaries = useMemo(
    () => summarizeMixedDisplayedYears(windowedPoints, { isoKey: "month" }),
    [windowedPoints]
  );

  const windowAverages = useMemo(() => {
    const historicalValues = windowedPoints
      .map((row) => row.actual)
      .filter((value) => Number.isFinite(value));
    const forecastValues = windowedPoints
      .map((row) => row.forecast)
      .filter((value) => Number.isFinite(value));
    const historicalAverage = historicalValues.length
      ? historicalValues.reduce((sum, value) => sum + value, 0) / historicalValues.length
      : null;
    const forecastAverage = Number.isFinite(Number(trend?.forecast_average))
      ? Number(trend.forecast_average)
      : forecastValues.length
        ? forecastValues.reduce((sum, value) => sum + value, 0) / forecastValues.length
        : null;
    const difference =
      historicalAverage == null || forecastAverage == null ? null : forecastAverage - historicalAverage;
    const differencePercent =
      difference == null || historicalAverage == null || historicalAverage === 0
        ? null
        : (difference / historicalAverage) * 100;
    return { historicalAverage, forecastAverage, difference, differencePercent };
  }, [windowedPoints, trend?.forecast_average]);

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
          Historical expenditure and predicted budget on one continuous monthly timeline. Missing months stay blank; series do not overlap or invent values across gaps.
        </p>
      </div>

      <section className="analytics-meta-grid" aria-label="Comparison periods">
        <div className="analytics-meta-item">
          <span>Forecast category</span>
          <strong>{trend.category || "—"}</strong>
        </div>
        <div className="analytics-meta-item">
          <span>Historical period displayed</span>
          <strong>
            {displayedActualRange?.startIso && displayedActualRange?.endIso
              ? `${monthTitle(displayedActualRange.startIso)} – ${monthTitle(displayedActualRange.endIso)}`
              : "—"}
          </strong>
        </div>
        <div className="analytics-meta-item">
          <span>Forecast period</span>
          <strong>
            {trend.forecast_start && trend.forecast_end
              ? `${monthTitle(trend.forecast_start)} – ${monthTitle(trend.forecast_end)}`
              : "—"}
          </strong>
        </div>
      </section>

      <ul className="analytics-kpis" aria-label="Budget trend snapshot">
        <li>
          <span>Historical monthly average</span>
          <strong>{formatAmount(windowAverages.historicalAverage, amountUnit)}</strong>
          <em className="analytics-kpi-note">
            {historyView === HISTORY_RANGE_ALL
              ? "Monthly average over All history months shown"
              : "Monthly average over the Last 24 months shown"}
          </em>
        </li>
        <li>
          <span>Forecast monthly average</span>
          <strong>{formatAmount(windowAverages.forecastAverage, amountUnit)}</strong>
          <em className="analytics-kpi-note">Monthly average across forecast months only</em>
        </li>
        <li>
          <span>Monthly-average difference</span>
          <strong className="analytics-comparison-delta">
            {Number.isFinite(windowAverages.difference)
              ? `${formatAmount(windowAverages.difference, amountUnit)} (${formatPercentChange(windowAverages.differencePercent)})`
              : "—"}
          </strong>
          <em className="analytics-kpi-note">Higher or lower expenditure is informational, not a performance score</em>
        </li>
        <li>
          <span>Historical trend</span>
          <strong>{trend.historical_trend?.label || "—"}</strong>
          <em className="analytics-kpi-note">Direction across displayed historical months</em>
        </li>
        <li>
          <span>Forecast trend</span>
          <strong>{trend.forecast_trend?.label || "—"}</strong>
          <em className="analytics-kpi-note">Direction across forecast months</em>
        </li>
      </ul>

      <article className="card analytics-main">
        <div className="analytics-main-head">
          <div>
            <h3>Historical versus forecast timeline</h3>
            <p className="muted">
              {historyView === HISTORY_RANGE_LAST_24
                ? `${formatDisplayedRangeLabel(historyWindow)}. Forecast period is unchanged when this range switches.`
                : `${formatDisplayedRangeLabel(historyWindow || displayedActualRange)}. Full available actuals plus the selected forecast period.`}
            </p>
            {showRange ? <ExpectedRangeLabel /> : null}
          </div>
          <div className="analytics-history-view" role="group" aria-label="History window">
            <button
              type="button"
              className={historyView === HISTORY_RANGE_LAST_24 ? "active" : ""}
              onClick={() => setHistoryView(HISTORY_RANGE_LAST_24)}
            >
              Last 24 months
            </button>
            <button
              type="button"
              className={historyView === HISTORY_RANGE_ALL ? "active" : ""}
              onClick={() => setHistoryView(HISTORY_RANGE_ALL)}
            >
              All history
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
