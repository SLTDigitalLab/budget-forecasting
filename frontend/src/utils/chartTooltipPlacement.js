export const TOOLTIP_GAP = 12;
export const TOOLTIP_PADDING = 12;
export const DEFAULT_TOOLTIP_WIDTH = 280;
export const DEFAULT_TOOLTIP_HEIGHT = 210;

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function resolveTooltipWidth(containerWidth, measuredWidth) {
  const box = Math.max(0, Number(containerWidth) || 0);
  const available = Math.max(0, box - TOOLTIP_PADDING * 2);
  const preferred = Number.isFinite(Number(measuredWidth)) && Number(measuredWidth) > 0
    ? Number(measuredWidth)
    : DEFAULT_TOOLTIP_WIDTH;
  const cap = box > 0 && box < 480 ? 300 : 320;
  if (available < 1) {
    return Math.min(preferred, cap);
  }
  return Math.min(preferred, cap, available);
}

/**
 * Place a tooltip beside a chart-local point, keeping it inside the chart box.
 * Prefer right, then left, then above/below. Used when the overlay stays in-chart.
 */
export function placeChartTooltip({
  pointX,
  pointY,
  containerWidth,
  containerHeight,
  tooltipWidth,
  tooltipHeight,
  gap = TOOLTIP_GAP,
  padding = TOOLTIP_PADDING,
} = {}) {
  const width = resolveTooltipWidth(containerWidth, tooltipWidth);
  const height = Math.min(
    Number.isFinite(Number(tooltipHeight)) && Number(tooltipHeight) > 0
      ? Number(tooltipHeight)
      : DEFAULT_TOOLTIP_HEIGHT,
    Math.max(0, (Number(containerHeight) || DEFAULT_TOOLTIP_HEIGHT) - padding * 2)
  );
  const boxWidth = Math.max(0, Number(containerWidth) || 0);
  const boxHeight = Math.max(0, Number(containerHeight) || 0);
  const xPoint = Number(pointX) || 0;
  const yPoint = Number(pointY) || 0;
  const spacing = Number.isFinite(Number(gap)) ? Number(gap) : TOOLTIP_GAP;
  const inset = Number.isFinite(Number(padding)) ? Number(padding) : TOOLTIP_PADDING;

  const canPlaceRight = xPoint + spacing + width <= boxWidth - inset;
  const canPlaceLeft = xPoint - spacing - width >= inset;
  let x;
  let y;
  let side;
  if (canPlaceRight) {
    x = xPoint + spacing;
    y = yPoint - height / 2;
    side = "right";
  } else if (canPlaceLeft) {
    x = xPoint - width - spacing;
    y = yPoint - height / 2;
    side = "left";
  } else if (yPoint - spacing - height >= inset) {
    x = (boxWidth - width) / 2;
    y = yPoint - spacing - height;
    side = "above";
  } else {
    x = (boxWidth - width) / 2;
    y = yPoint + spacing;
    side = "below";
  }

  return {
    x: clamp(x, inset, Math.max(inset, boxWidth - width - inset)),
    y: clamp(y, inset, Math.max(inset, boxHeight - height - inset)),
    width,
    height,
    side,
  };
}

/**
 * Viewport-aware placement using the tooltip's measured size.
 * Prefer right of the anchor, then left, then above/below. ~12px edge padding.
 */
export function placeViewportTooltip({
  clientX,
  clientY,
  tooltipWidth = DEFAULT_TOOLTIP_WIDTH,
  tooltipHeight = DEFAULT_TOOLTIP_HEIGHT,
  viewportWidth = 0,
  viewportHeight = 0,
  gap = TOOLTIP_GAP,
  padding = TOOLTIP_PADDING,
} = {}) {
  const width = Math.max(1, Number(tooltipWidth) || DEFAULT_TOOLTIP_WIDTH);
  const height = Math.max(1, Number(tooltipHeight) || DEFAULT_TOOLTIP_HEIGHT);
  const viewWidth = Math.max(0, Number(viewportWidth) || 0);
  const viewHeight = Math.max(0, Number(viewportHeight) || 0);
  const spacing = Number.isFinite(Number(gap)) ? Number(gap) : TOOLTIP_GAP;
  const inset = Number.isFinite(Number(padding)) ? Number(padding) : TOOLTIP_PADDING;
  const ax = Number(clientX) || 0;
  const ay = Number(clientY) || 0;

  const canPlaceRight = !viewWidth || ax + spacing + width <= viewWidth - inset;
  const canPlaceLeft = !viewWidth || ax - spacing - width >= inset;
  let x;
  let y;
  let side;

  if (canPlaceRight) {
    x = ax + spacing;
    y = ay - height / 2;
    side = "right";
  } else if (canPlaceLeft) {
    x = ax - width - spacing;
    y = ay - height / 2;
    side = "left";
  } else if (!viewHeight || ay - spacing - height >= inset) {
    x = ax - width / 2;
    y = ay - spacing - height;
    side = "above";
  } else {
    x = ax - width / 2;
    y = ay + spacing;
    side = "below";
  }

  if (viewWidth) {
    x = clamp(x, inset, Math.max(inset, viewWidth - width - inset));
  }
  if (viewHeight) {
    y = clamp(y, inset, Math.max(inset, viewHeight - height - inset));
  }

  return { x, y, width, height, side };
}

/** @deprecated Prefer placeViewportTooltip; kept for existing call sites/tests. */
export function placeFixedTooltip(args = {}) {
  const placed = placeViewportTooltip({
    ...args,
    padding: args.padding ?? 8,
  });
  return {
    x: placed.x,
    y: placed.y,
    width: placed.width,
    height: placed.height,
  };
}
