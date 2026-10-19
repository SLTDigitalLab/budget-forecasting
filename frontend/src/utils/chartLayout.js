/**
 * Shared Recharts spacing so axis titles sit beside ticks, not on top of them.
 *
 * Layout for a value Y-axis:
 *   [small left pad] [YAxis width: rotated unit title | gap | numeric ticks] [plot]
 */

export const CARTESIAN_CHART_MARGIN = Object.freeze({
  top: 12,
  right: 16,
  left: 10,
  bottom: 8,
});

export const AMOUNT_CHART_MARGIN = Object.freeze({
  top: 12,
  right: 16,
  left: 12,
  bottom: 8,
});

/**
 * Title sits on the left of this band; ticks sit on the right.
 * Must be wide enough for "LKR Mn" plus ticks such as 1,000,000.
 */
export const VALUE_Y_AXIS_WIDTH = 96;
export const VALUE_Y_AXIS_TICK_MARGIN = 8;
export const VALUE_AXIS_TITLE_OFFSET = 4;

/** Generate Forecast ticks include the full "LKR 1,000.00 Mn" string. */
export const AMOUNT_TICK_Y_AXIS_WIDTH = 148;

export const CATEGORY_Y_AXIS_WIDTH = 168;

export function cartesianChartMargin(overrides = {}) {
  return { ...CARTESIAN_CHART_MARGIN, ...overrides };
}

export function amountChartMargin(overrides = {}) {
  return { ...AMOUNT_CHART_MARGIN, ...overrides };
}

export function valueAxisTitle(unit, fill = DEFAULT_CHART_PALETTE.axis) {
  return {
    value: unit,
    angle: -90,
    position: "insideLeft",
    offset: VALUE_AXIS_TITLE_OFFSET,
    style: {
      textAnchor: "middle",
      fontSize: 12,
      fill,
    },
  };
}

export function valueXAxisTitle(unit, fill = DEFAULT_CHART_PALETTE.axis) {
  return {
    value: unit,
    position: "insideBottom",
    offset: -4,
    style: {
      textAnchor: "middle",
      fontSize: 12,
      fill,
    },
  };
}

export function valueYAxisProps(unit, overrides = {}) {
  const { axisFill, ...rest } = overrides;
  return {
    width: VALUE_Y_AXIS_WIDTH,
    tickMargin: VALUE_Y_AXIS_TICK_MARGIN,
    tick: { fontSize: 11, fill: axisFill || DEFAULT_CHART_PALETTE.axis },
    label: valueAxisTitle(unit, axisFill || DEFAULT_CHART_PALETTE.axis),
    ...rest,
  };
}

export function amountTickYAxisProps(overrides = {}) {
  return {
    width: AMOUNT_TICK_Y_AXIS_WIDTH,
    tickMargin: 6,
    tick: { fontSize: 12 },
    ...overrides,
  };
}

export function estimateTickLabelWidth(text, fontSize = 11) {
  const label = String(text ?? "");
  return Math.ceil(label.length * fontSize * 0.62) + VALUE_Y_AXIS_TICK_MARGIN;
}

export function yAxisFitsTicks(sampleTicks) {
  return sampleTicks.every((tick) => estimateTickLabelWidth(tick) <= VALUE_Y_AXIS_WIDTH);
}

/**
 * Light-theme defaults (also used by unit tests). Runtime charts read CSS tokens.
 * Indigo + Aqua: historical indigo, forecast cyan/aqua, seasonal lavender.
 * Avoid near-black fills for data series.
 */
export const FORECAST_BAR_FILL = "#06B6D4";
export const HISTORICAL_BAR_FILL = "#6366F1";
export const INTERVAL_BAR_STROKE = "#67E8F9";
export const CHART_SERIES_PURPLE = "#A78BFA";
export const CHART_SERIES_ORANGE = "#EA580C";
export const SEASONAL_BAR_FILL = CHART_SERIES_PURPLE;

export const DEFAULT_CHART_PALETTE = Object.freeze({
  forecast: FORECAST_BAR_FILL,
  historical: HISTORICAL_BAR_FILL,
  interval: INTERVAL_BAR_STROKE,
  purple: CHART_SERIES_PURPLE,
  seasonal: SEASONAL_BAR_FILL,
  orange: CHART_SERIES_ORANGE,
  grid: "#e6eef6",
  axis: "#5b6b7c",
  zero: "#8aa0b8",
  cursor: "rgba(99, 102, 241, 0.08)",
  negative: "#c0392b",
  tick: "#5b6b7c",
  legend: "#1c2a3a",
});

/** Shared solid bar presentation — never apply FORECAST_LINE_DASH to bars. */
export const SOLID_BAR_RADIUS = Object.freeze([3, 3, 0, 0]);

function cssToken(name, fallback) {
  if (typeof document === "undefined") {
    return fallback;
  }
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** Resolve chart colors from theme CSS variables without refetching data. */
export function getChartPalette() {
  return {
    forecast: cssToken("--chart-forecast", DEFAULT_CHART_PALETTE.forecast),
    historical: cssToken("--chart-historical", DEFAULT_CHART_PALETTE.historical),
    interval: cssToken("--chart-interval", DEFAULT_CHART_PALETTE.interval),
    purple: cssToken("--chart-purple", DEFAULT_CHART_PALETTE.purple),
    seasonal: cssToken("--chart-seasonal", DEFAULT_CHART_PALETTE.seasonal),
    orange: cssToken("--chart-orange", DEFAULT_CHART_PALETTE.orange),
    grid: cssToken("--chart-grid", DEFAULT_CHART_PALETTE.grid),
    axis: cssToken("--chart-axis", DEFAULT_CHART_PALETTE.axis),
    zero: cssToken("--chart-zero", DEFAULT_CHART_PALETTE.zero),
    cursor: cssToken("--chart-cursor", DEFAULT_CHART_PALETTE.cursor),
    negative: cssToken("--chart-negative", DEFAULT_CHART_PALETTE.negative),
    tick: cssToken("--chart-axis", DEFAULT_CHART_PALETTE.tick),
    legend: cssToken("--chart-legend", DEFAULT_CHART_PALETTE.legend),
  };
}

/** Dashed stroke for forecast LINES only — never apply to Bar series. */
export const FORECAST_LINE_DASH = "6 4";


/**
 * Whisker offsets around a plotted value. Missing bounds stay null so the
 * chart leaves a gap instead of drawing a zero-width interval.
 */
function finiteChartValue(value) {
  if (value == null || value === "") {
    return null;
  }
  const point = Number(value);
  return Number.isFinite(point) ? point : null;
}

export function intervalError(value, lower, upper) {
  const point = finiteChartValue(value);
  const low = finiteChartValue(lower);
  const high = finiteChartValue(upper);
  if (point == null || low == null || high == null) {
    return null;
  }
  return [Math.max(0, point - low), Math.max(0, high - point)];
}

export function withIntervalError(rows, valueKey, lowerKey = "lower", upperKey = "upper") {
  return (rows || []).map((row) => ({
    ...row,
    intervalError: intervalError(row?.[valueKey], row?.[lowerKey], row?.[upperKey]),
  }));
}
