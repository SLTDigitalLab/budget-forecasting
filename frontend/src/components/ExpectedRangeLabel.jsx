import { Info } from "lucide-react";

export const EXPECTED_RANGE_LABEL = "Expected Range (90%)";
export const EXPECTED_RANGE_TOOLTIP =
  "The shaded area shows the expected range around the forecast based on historical forecasting performance.";
export const EXPECTED_RANGE_UNAVAILABLE = "Expected range is not available for this month.";

export default function ExpectedRangeLabel() {
  return (
    <p className="muted expected-range-label">
      <span>{EXPECTED_RANGE_LABEL}</span>
      <span className="expected-range-info" title={EXPECTED_RANGE_TOOLTIP}>
        <Info size={14} aria-hidden="true" />
        <span className="sr-only">{EXPECTED_RANGE_TOOLTIP}</span>
      </span>
    </p>
  );
}
