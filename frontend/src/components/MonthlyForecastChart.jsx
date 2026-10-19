import { useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ErrorBar,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import useChartPalette from "../hooks/useChartPalette";
import {
  amountChartMargin,
  amountTickYAxisProps,
  SOLID_BAR_RADIUS,
  valueAxisTitle,
  withIntervalError,
} from "../utils/chartLayout";
import { axisUnitLabel, formatAmount, formatAxisTick, isPresentAmount } from "../utils/forecastDisplay";
import {
  formatTooltipChange,
  specificMonthAverageLabel,
  specificMonthChangeLabel,
  tooltipChangeTone,
} from "../utils/forecastHistoryBaseline";
import {
  isYearTotalRow,
  summarizeDisplayedYears,
} from "../utils/chartYearTotals";
import BoundedChartTooltip from "./BoundedChartTooltip";
import ChartYearSummaries from "./ChartYearSummaries";
import EmptyState from "./EmptyState";
import LoadingState from "./LoadingState";

function finiteValues(data, keys) {
  const amounts = [];
  (data || []).forEach((row) => {
    keys.forEach((key) => {
      const value = Number(row?.[key]);
      if (Number.isFinite(value)) {
        amounts.push(value);
      }
    });
  });
  return amounts;
}

function paddedDomain(data) {
  const amounts = finiteValues(data, ["amount", "lower", "upper"]);
  if (!amounts.length) {
    return ["auto", "auto"];
  }
  const min = Math.min(...amounts, 0);
  const max = Math.max(...amounts, 0);
  const span = max - min;
  const padding = span < 1e-9 ? Math.max(Math.abs(max) * 0.08, 1) : span * 0.08;
  return [min - padding, max + padding];
}

function hasBounds(data) {
  return (data || []).some(
    (row) => !isYearTotalRow(row) && isPresentAmount(row.lower) && isPresentAmount(row.upper)
  );
}

function TooltipChange({ amount, percent, amountUnit }) {
  const tone = tooltipChangeTone(amount, percent);
  return (
    <div className={`chart-tooltip-value chart-tooltip-change is-${tone}`}>
      {formatTooltipChange(amount, percent, amountUnit)}
    </div>
  );
}

export function ForecastPointTooltip({ point, label, amountUnit }) {
  const hasSpecific = Number.isFinite(Number(point?.specificAverage)) && Number(point?.specificCount) > 0;
  return (
    <div className="chart-tooltip chart-tooltip-overlay">
      <div className="chart-tooltip-month">{label}</div>
      <div className="chart-tooltip-block">
        <div className="chart-tooltip-series">Forecast</div>
        <div className="chart-tooltip-value chart-tooltip-forecast">{formatAmount(point?.amount, amountUnit)}</div>
      </div>
      <div className="chart-tooltip-block">
        <div className="chart-tooltip-series">Vs Overall Average</div>
        <TooltipChange amount={point?.changeAmount} percent={point?.changePercent} amountUnit={amountUnit} />
      </div>
      <div className="chart-tooltip-block">
        <div className="chart-tooltip-series">{specificMonthAverageLabel(point?.isoMonth)}</div>
        <div className="chart-tooltip-value">
          {hasSpecific ? formatAmount(point.specificAverage, amountUnit) : "Unavailable"}
        </div>
      </div>
      <div className="chart-tooltip-block">
        <div className="chart-tooltip-series">{specificMonthChangeLabel(point?.isoMonth)}</div>
        <TooltipChange
          amount={hasSpecific ? point.specificChangeAmount : null}
          percent={hasSpecific ? point.specificChangePercent : null}
          amountUnit={amountUnit}
        />
      </div>
    </div>
  );
}

function ForecastOnlyChart({ data, amountUnit }) {
  const palette = useChartPalette();
  const containerRef = useRef(null);
  const [active, setActive] = useState(null);
  const chartRows = useMemo(
    () => (Array.isArray(data) ? data : []).filter((row) => !isYearTotalRow(row)),
    [data]
  );
  const yearSummaries = useMemo(
    () => summarizeDisplayedYears(chartRows, {
      isoKey: "isoMonth",
      valueKey: "amount",
      seriesKind: "forecast",
    }),
    [chartRows]
  );
  const showInterval = hasBounds(chartRows);
  const domain = paddedDomain(chartRows);

  return (
    <div className="chart-area chart-area-tooltip-host analytics-expenditure-panel">
      <ChartYearSummaries summaries={yearSummaries} amountUnit={amountUnit} />
      <div className="chart-area analytics-chart-fit" ref={containerRef} style={{ minHeight: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={withIntervalError(chartRows, "amount")}
            margin={amountChartMargin({ bottom: 16, top: 8, right: 12 })}
            barCategoryGap="18%"
            barGap={6}
            onMouseMove={(state) => {
              if (!state?.isTooltipActive || !state.activePayload?.length || !state.activeCoordinate) {
                setActive(null);
                return;
              }
              const point = state.activePayload[0].payload;
              setActive({
                point,
                label: state.activeLabel,
                x: state.activeCoordinate.x,
                y: state.activeCoordinate.y,
              });
            }}
            onMouseLeave={() => setActive(null)}
          >
            <CartesianGrid stroke={palette.grid} vertical={false} />
            <XAxis
              dataKey="month"
              tick={{ fontSize: 11, fill: palette.tick }}
              tickMargin={8}
              minTickGap={22}
              interval="preserveStartEnd"
            />
            <YAxis
              {...amountTickYAxisProps({ tick: { fontSize: 12, fill: palette.tick } })}
              width={72}
              domain={domain}
              allowDataOverflow={false}
              tickFormatter={(value) => formatAxisTick(value, amountUnit)}
              label={valueAxisTitle(axisUnitLabel(amountUnit), palette.axis)}
            />
            <ReferenceLine y={0} stroke={palette.zero} strokeDasharray="3 3" ifOverflow="extendDomain" />
            <Tooltip cursor={{ fill: palette.cursor }} content={() => null} wrapperStyle={{ display: "none" }} />
            <Bar
              dataKey="amount"
              name="Forecast"
              fill={palette.forecast}
              maxBarSize={28}
              radius={SOLID_BAR_RADIUS}
              isAnimationActive={false}
            >
              {chartRows.map((row, index) => (
                <Cell
                  key={`${row.month || index}`}
                  fill={Number(row.amount) < 0 ? palette.negative : palette.forecast}
                />
              ))}
              {showInterval ? (
                <ErrorBar dataKey="intervalError" width={6} strokeWidth={1.25} stroke={palette.interval} direction="y" />
              ) : null}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <BoundedChartTooltip active={Boolean(active)} anchor={active} hostRef={containerRef}>
          {active ? (
            <ForecastPointTooltip point={active.point} label={active.label} amountUnit={amountUnit} />
          ) : null}
        </BoundedChartTooltip>
      </div>
    </div>
  );
}

export default function MonthlyForecastChart({ data, loading, amountUnit = "LKR" }) {
  if (loading) {
    return <LoadingState label="Loading monthly forecast..." />;
  }
  if (!data?.length) {
    return <EmptyState message="Generate a forecast to view the monthly trend." />;
  }
  return <ForecastOnlyChart data={data} amountUnit={amountUnit} />;
}

/** Presentation helper for tests — monthly rows only, no injected year totals. */
export function buildCombinedForecastChartRows(data) {
  return (Array.isArray(data) ? data : []).filter((row) => !isYearTotalRow(row));
}
