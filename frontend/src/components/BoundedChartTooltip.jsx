import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  DEFAULT_TOOLTIP_HEIGHT,
  DEFAULT_TOOLTIP_WIDTH,
  placeViewportTooltip,
} from "../utils/chartTooltipPlacement";

/**
 * Viewport-aware chart tooltip rendered in a portal so overflow:hidden chart
 * ancestors cannot clip November/December (or any edge) content.
 *
 * `anchor` is chart-local { x, y }; pass `hostRef` so coordinates convert to
 * client space during scroll/resize.
 */
export default function BoundedChartTooltip({
  active,
  anchor,
  hostRef,
  children,
  className = "chart-tooltip-overlay-wrap chart-tooltip-portal",
}) {
  const tooltipRef = useRef(null);
  const [size, setSize] = useState({
    width: DEFAULT_TOOLTIP_WIDTH,
    height: DEFAULT_TOOLTIP_HEIGHT,
  });
  const [placement, setPlacement] = useState(null);

  useLayoutEffect(() => {
    if (!active || !anchor || typeof document === "undefined") {
      setPlacement(null);
      return undefined;
    }
    const host = hostRef?.current;
    const hostRect = host?.getBoundingClientRect?.();
    const clientX = (hostRect?.left || 0) + (Number(anchor.x) || 0);
    const clientY = (hostRect?.top || 0) + (Number(anchor.y) || 0);

    function measureAndPlace() {
      const node = tooltipRef.current;
      const width = node?.offsetWidth || size.width || DEFAULT_TOOLTIP_WIDTH;
      const height = node?.offsetHeight || size.height || DEFAULT_TOOLTIP_HEIGHT;
      if (node) {
        const next = { width: node.offsetWidth, height: node.offsetHeight };
        if (Math.abs(next.width - size.width) >= 1 || Math.abs(next.height - size.height) >= 1) {
          setSize(next);
        }
      }
      setPlacement(
        placeViewportTooltip({
          clientX,
          clientY,
          tooltipWidth: width,
          tooltipHeight: height,
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
        })
      );
    }

    measureAndPlace();
    const frame = requestAnimationFrame(measureAndPlace);
    window.addEventListener("resize", measureAndPlace);
    window.addEventListener("scroll", measureAndPlace, true);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", measureAndPlace);
      window.removeEventListener("scroll", measureAndPlace, true);
    };
  }, [active, anchor, hostRef, size.width, size.height, children]);

  if (!active || !anchor || typeof document === "undefined") {
    return null;
  }

  const style = placement
    ? {
        position: "fixed",
        left: placement.x,
        top: placement.y,
        width: placement.width,
        zIndex: 80,
        pointerEvents: "none",
      }
    : {
        position: "fixed",
        left: -9999,
        top: -9999,
        visibility: "hidden",
        pointerEvents: "none",
      };

  return createPortal(
    <div ref={tooltipRef} className={className} style={style} role="tooltip">
      {children}
    </div>,
    document.body
  );
}
