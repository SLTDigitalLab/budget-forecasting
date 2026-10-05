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

export function valueAxisTitle(unit) {
  return {
    value: unit,
    angle: -90,
    position: "insideLeft",
    offset: VALUE_AXIS_TITLE_OFFSET,
    style: {
      textAnchor: "middle",
      fontSize: 12,
      fill: "#5b6b7c",
    },
  };
}

export function valueXAxisTitle(unit) {
  return {
    value: unit,
    position: "insideBottom",
    offset: -4,
    style: {
      textAnchor: "middle",
      fontSize: 12,
      fill: "#5b6b7c",
    },
  };
}

export function valueYAxisProps(unit, overrides = {}) {
  return {
    width: VALUE_Y_AXIS_WIDTH,
    tickMargin: VALUE_Y_AXIS_TICK_MARGIN,
    tick: { fontSize: 11 },
    label: valueAxisTitle(unit),
    ...overrides,
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
