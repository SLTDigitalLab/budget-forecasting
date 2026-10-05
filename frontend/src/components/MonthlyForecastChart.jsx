import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { amountChartMargin, amountTickYAxisProps } from "../utils/chartLayout";
import { formatAmount, isPresentAmount } from "../utils/forecastDisplay";
import {
  formatTooltipChange,
  specificMonthAverageLabel,
  specificMonthChangeLabel,
  tooltipChangeTone,
} from "../utils/forecastHistoryBaseline";
import { DEFAULT_TOOLTIP_HEIGHT, DEFAULT_TOOLTIP_WIDTH, placeChartTooltip } from "../utils/chartTooltipPlacement";
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
  const min = Math.min(...amounts);
  const max = Math.max(...amounts);
  const span = max - min;
  const padding = span < 1e-9 ? Math.max(Math.abs(max) * 0.08, 1) : span * 0.08;
  return [min - padding, max + padding];
}

function hasBounds(data) {
  return (data || []).some((row) => isPresentAmount(row.lower) && isPresentAmount(row.upper));
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
  const containerRef = useRef(null);
  const tooltipRef = useRef(null);
  const [active, setActive] = useState(null);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 270 });
  const [tooltipSize, setTooltipSize] = useState({
    width: DEFAULT_TOOLTIP_WIDTH,
    height: DEFAULT_TOOLTIP_HEIGHT,
  });
  const showInterval = hasBounds(data);
  const domain = paddedDomain(data);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) {
      return undefined;
    }
    function measure() {
      setContainerSize({ width: node.clientWidth, height: node.clientHeight });
    }
    measure();
    if (typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const node = tooltipRef.current;
    if (!node) {
      return;
    }
    const nextWidth = node.offsetWidth;
    const nextHeight = node.offsetHeight;
    setTooltipSize((prev) => {
      if (Math.abs(prev.width - nextWidth) < 1 && Math.abs(prev.height - nextHeight) < 1) {
        return prev;
      }
      return { width: nextWidth, height: nextHeight };
    });
  }, [active, containerSize.width, containerSize.height]);

  const placement = active
    ? placeChartTooltip({
        pointX: active.x,
        pointY: active.y,
        containerWidth: containerSize.width,
        containerHeight: containerSize.height,
        tooltipWidth: tooltipSize.width,
        tooltipHeight: tooltipSize.height,
      })
    : null;

  return (
    <div className="chart-area chart-area-tooltip-host" ref={containerRef}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={amountChartMargin()}
          onMouseMove={(state) => {
            if (!state?.isTooltipActive || !state.activePayload?.length || !state.activeCoordinate) {
              setActive(null);
              return;
            }
            setActive({
              point: state.activePayload[0].payload,
              label: state.activeLabel,
              x: state.activeCoordinate.x,
              y: state.activeCoordinate.y,
            });
          }}
          onMouseLeave={() => setActive(null)}
        >
          <defs>
            <linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#1f7ae0" stopOpacity={0.24} />
              <stop offset="95%" stopColor="#1ec8e0" stopOpacity={0.04} />
            </linearGradient>
            <linearGradient id="intervalFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#1f7ae0" stopOpacity={0.18} />
              <stop offset="95%" stopColor="#1f7ae0" stopOpacity={0.04} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#e6eef6" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 12 }} tickMargin={6} />
          <YAxis
            {...amountTickYAxisProps()}
            domain={domain}
            allowDataOverflow={false}
            tickFormatter={(value) => formatAmount(value, amountUnit)}
          />
          <Tooltip
            cursor={{ stroke: "#1f7ae0", strokeWidth: 1 }}
            content={() => null}
            wrapperStyle={{ display: "none" }}
          />
          {showInterval ? (
            <Area
              type="linear"
              dataKey="upper"
              name="Upper interval"
              stroke="none"
              fill="url(#intervalFill)"
              isAnimationActive={false}
              connectNulls={false}
            />
          ) : null}
          {showInterval ? (
            <Area
              type="linear"
              dataKey="lower"
              name="Lower interval"
              stroke="none"
              fill="#ffffff"
              fillOpacity={1}
              isAnimationActive={false}
              connectNulls={false}
            />
          ) : null}
          <Area
            type="linear"
            dataKey="amount"
            name="Forecast"
            stroke="#1f7ae0"
            strokeWidth={3}
            fill="url(#forecastFill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
      {active && placement ? (
        <div
          ref={tooltipRef}
          className="chart-tooltip-overlay-wrap"
          style={{ left: placement.x, top: placement.y, width: placement.width }}
        >
          <ForecastPointTooltip point={active.point} label={active.label} amountUnit={amountUnit} />
        </div>
      ) : null}
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
