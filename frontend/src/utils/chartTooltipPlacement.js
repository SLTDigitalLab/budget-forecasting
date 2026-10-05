export const TOOLTIP_GAP = 12;
export const DEFAULT_TOOLTIP_WIDTH = 280;
export const DEFAULT_TOOLTIP_HEIGHT = 210;

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function resolveTooltipWidth(containerWidth, measuredWidth) {
  const box = Math.max(0, Number(containerWidth) || 0);
  const available = Math.max(0, box - 16);
  const preferred = Number.isFinite(Number(measuredWidth)) && Number(measuredWidth) > 0
    ? Number(measuredWidth)
    : DEFAULT_TOOLTIP_WIDTH;
  const cap = box > 0 && box < 480 ? 300 : 320;
  if (available < 1) {
    return Math.min(preferred, cap);
  }
  return Math.min(preferred, cap, available);
}

export function placeChartTooltip({
  pointX,
  pointY,
  containerWidth,
  containerHeight,
  tooltipWidth,
  tooltipHeight,
  gap = TOOLTIP_GAP,
} = {}) {
  const width = resolveTooltipWidth(containerWidth, tooltipWidth);
  const height = Math.min(
    Number.isFinite(Number(tooltipHeight)) && Number(tooltipHeight) > 0
      ? Number(tooltipHeight)
      : DEFAULT_TOOLTIP_HEIGHT,
    Math.max(0, Number(containerHeight) || DEFAULT_TOOLTIP_HEIGHT)
  );
  const boxWidth = Math.max(0, Number(containerWidth) || 0);
  const boxHeight = Math.max(0, Number(containerHeight) || 0);
  const xPoint = Number(pointX) || 0;
  const yPoint = Number(pointY) || 0;
  const spacing = Number.isFinite(Number(gap)) ? Number(gap) : TOOLTIP_GAP;

  const canPlaceRight = xPoint + spacing + width <= boxWidth;
  const canPlaceLeft = xPoint - spacing - width >= 0;
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
  } else if (yPoint - spacing - height >= 0) {
    x = (boxWidth - width) / 2;
    y = yPoint - spacing - height;
    side = "above";
  } else {
    x = (boxWidth - width) / 2;
    y = yPoint + spacing;
    side = "below";
  }

  return {
    x: clamp(x, 0, Math.max(0, boxWidth - width)),
    y: clamp(y, 0, Math.max(0, boxHeight - height)),
    width,
    height,
    side,
  };
}
