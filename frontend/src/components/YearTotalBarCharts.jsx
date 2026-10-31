import { useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import useChartPalette from "../hooks/useChartPalette";
import { amountChartMargin, SOLID_BAR_RADIUS, valueAxisTitle } from "../utils/chartLayout";
import {
  forecastYearChartTitle,
  forecastYearRangeLabel,
  sharedYearTotalDomain,
  summarizeDisplayedYears,
  yearTotalFill,
} from "../utils/chartYearTotals";
import { axisUnitLabel, formatAmount, formatAxisTick } from "../utils/forecastDisplay";
import BoundedChartTooltip from "./BoundedChartTooltip";

function YearTotalTooltip({ summary, amountUnit }) {
  const title = forecastYearChartTitle(summary);
  const range = forecastYearRangeLabel(summary);
  return (
    <div className="chart-tooltip chart-tooltip-overlay">
      <div className="chart-tooltip-month">{title}</div>
      {range ? <div className="chart-tooltip-series muted">{range}</div> : null}
      <div className="chart-tooltip-block">
        <div className="chart-tooltip-series">Total</div>
        <div className="chart-tooltip-value chart-tooltip-forecast">
          {summary.total == null ? "Unavailable" : formatAmount(summary.total, amountUnit)}
        </div>
      </div>
      {summary.coverageLabel ? (
        <div className="chart-tooltip-block">
          <div className="chart-tooltip-series muted">{summary.coverageLabel}</div>
        </div>
      ) : null}
    </div>
  );
}

function YearTotalBarChart({ summary, amountUnit, domain }) {
  const palette = useChartPalette();
  const containerRef = useRef(null);
  const [active, setActive] = useState(null);
  const fill = yearTotalFill();
  const title = forecastYearChartTitle(summary);
  const range = forecastYearRangeLabel(summary);
  const category = String(summary.year);
  const chartData = useMemo(
    () => [
      {
        category,
        amount: summary.total,
        summary,
      },
    ],
    [category, summary]
  );
  const barRadius =
    Number(summary.total) < 0 ? [0, 0, 3, 3] : SOLID_BAR_RADIUS;

  return (
    <article className="combined-forecast-year-total" aria-label={title}>
      <div className="combined-forecast-year-total-header">
        <h3 className="combined-forecast-year-total-title">{title}</h3>
        {range ? <p className="combined-forecast-year-total-range muted">{range}</p> : null}
      </div>
      <div className="combined-forecast-year-total-chart chart-area-tooltip-host" ref={containerRef}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={chartData}
            margin={amountChartMargin({ top: 8, right: 6, bottom: 4, left: 2 })}
            barCategoryGap="28%"
            onMouseMove={(state) => {
              if (!state?.isTooltipActive || !state.activePayload?.length || !state.activeCoordinate) {
                setActive(null);
                return;
              }
              setActive({
                x: state.activeCoordinate.x,
                y: state.activeCoordinate.y,
              });
            }}
            onMouseLeave={() => setActive(null)}
          >
            <CartesianGrid stroke={palette.grid} vertical={false} />
            <XAxis
              dataKey="category"
              tick={{ fontSize: 11, fill: palette.tick }}
              tickMargin={6}
              axisLine={{ stroke: palette.axis }}
              tickLine={{ stroke: palette.axis }}
            />
            <YAxis
              width={52}
              domain={domain}
              allowDataOverflow={false}
              tick={{ fontSize: 10, fill: palette.tick }}
              tickFormatter={(value) => formatAxisTick(value, amountUnit)}
              label={valueAxisTitle(axisUnitLabel(amountUnit), palette.axis)}
            />
            <ReferenceLine y={0} stroke={palette.zero} strokeDasharray="3 3" ifOverflow="extendDomain" />
            <Tooltip cursor={{ fill: palette.cursor }} content={() => null} wrapperStyle={{ display: "none" }} />
            <Bar
              dataKey="amount"
              name="Total"
              maxBarSize={42}
              radius={barRadius}
              isAnimationActive={false}
            >
              <Cell fill={fill} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <BoundedChartTooltip active={Boolean(active)} anchor={active} hostRef={containerRef}>
          {active ? <YearTotalTooltip summary={summary} amountUnit={amountUnit} /> : null}
        </BoundedChartTooltip>
      </div>
      <div className="combined-forecast-year-total-value">
        {summary.total == null ? "Unavailable" : formatAmount(summary.total, amountUnit)}
      </div>
      {summary.coverageLabel ? (
        <div className="combined-forecast-year-total-coverage muted">{summary.coverageLabel}</div>
      ) : null}
    </article>
  );
}

/**
 * Red single-bar total charts for each calendar year, laid out side by side
 * with one shared zero-inclusive domain (independent of the monthly chart).
 */
export default function YearTotalBarCharts({ rows, amountUnit = "LKR" }) {
  const summaries = useMemo(
    () =>
      summarizeDisplayedYears(rows, {
        isoKey: "isoMonth",
        valueKey: "amount",
        seriesKind: "forecast",
      }),
    [rows]
  );
  const domain = useMemo(
    () => sharedYearTotalDomain(summaries.map((summary) => summary.total)),
    [summaries]
  );

  if (!summaries.length) {
    return null;
  }

  return (
    <div
      className="combined-forecast-charts-totals"
      aria-label="Forecast year totals"
      data-year-count={summaries.length}
    >
      {summaries.map((summary) => (
        <YearTotalBarChart
          key={summary.id || `${summary.year}-${summary.startIso}-${summary.endIso}`}
          summary={summary}
          amountUnit={amountUnit}
          domain={domain}
        />
      ))}
    </div>
  );
}
