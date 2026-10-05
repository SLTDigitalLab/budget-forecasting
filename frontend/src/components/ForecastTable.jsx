import { Minus, TrendingDown, TrendingUp } from "lucide-react";
import { formatAmount, formatBoundAmount, isPresentAmount } from "../utils/forecastDisplay";
import {
  SPECIFIC_MONTH_UNAVAILABLE,
  calendarMonthName,
  changeVsHistory,
  formatStackedChange,
  historicalAverageCaption,
} from "../utils/forecastHistoryBaseline";
import { isoToLabel } from "../utils/months";
import EmptyState from "./EmptyState";
import LoadingState from "./LoadingState";

function changeMeta(amount, percent, amountUnit, unavailableLabel = "") {
  if (unavailableLabel) {
    return {
      amount: unavailableLabel,
      percent: "",
      className: "change change-unavailable",
      Icon: Minus,
      direction: unavailableLabel,
      title: unavailableLabel,
    };
  }
  const stacked = formatStackedChange(amount, percent, amountUnit);
  if (!Number.isFinite(amount)) {
    return {
      amount: stacked.amount,
      percent: stacked.percent,
      className: "change",
      Icon: Minus,
      direction: "Change unavailable",
      title: "",
    };
  }
  if (amount === 0 || (Number.isFinite(percent) && Math.abs(percent) < 0.005)) {
    return {
      ...stacked,
      className: "change",
      Icon: Minus,
      direction: "No change versus history",
      title: "",
    };
  }
  if (amount > 0) {
    return {
      ...stacked,
      className: "change up",
      Icon: TrendingUp,
      direction: "Increase versus history",
      title: "",
    };
  }
  return {
    ...stacked,
    className: "change down",
    Icon: TrendingDown,
    direction: "Decrease versus history",
    title: "",
  };
}

function specificMonthTitle(row, amountUnit) {
  const name = calendarMonthName(row.month);
  const count = Number(row.specific_month_history_count);
  const average = Number(row.specific_month_historical_average);
  if (!name || !Number.isFinite(average) || count < 1) {
    return SPECIFIC_MONTH_UNAVAILABLE;
  }
  const countLabel = count === 1 ? "1 month" : `${count} months`;
  return `Historical ${name} Average (${countLabel}): ${formatAmount(average, amountUnit)}`;
}

function ChangeCell({ meta }) {
  const Icon = meta.Icon;
  return (
    <td className={`comparison-cell ${meta.className}`} title={meta.title || undefined}>
      <span className="change-cell">
        <Icon size={16} aria-hidden="true" />
        <span className="change-stack">
          <span className="change-amount">{meta.amount}</span>
          {meta.percent ? <span className="change-percent">{meta.percent}</span> : null}
        </span>
        <span className="sr-only"> {meta.direction}</span>
      </span>
    </td>
  );
}

export default function ForecastTable({
  monthlyForecasts,
  overallTotal,
  amountUnit = "LKR",
  loading = false,
  showStatus = false,
  historicalAverage = null,
  historicalMonthCount = 0,
  showHistoryComparisons = false,
}) {
  if (loading) {
    return <LoadingState label="Loading monthly table..." />;
  }
  if (!monthlyForecasts?.length) {
    return <EmptyState message="Generate a forecast to view the monthly breakdown." />;
  }
  const showBounds =
    !showHistoryComparisons &&
    monthlyForecasts.some((row) => isPresentAmount(row.lower_bound) || isPresentAmount(row.upper_bound));
  const showComparisons =
    showHistoryComparisons &&
    (Number.isFinite(Number(historicalAverage)) ||
      monthlyForecasts.some((row) => Number(row.specific_month_history_count) > 0));
  const baselineLabel = Number.isFinite(Number(historicalAverage))
    ? `${historicalAverageCaption(historicalMonthCount)}: ${formatAmount(historicalAverage, amountUnit)}`
    : "";

  return (
    <div className="monthly-details-scroll">
      {baselineLabel ? <p className="muted forecast-history-baseline">{baselineLabel}</p> : null}
      <div className="table-wrap">
      <table
        className={["monthly-details-table", showComparisons ? "has-comparisons" : ""].filter(Boolean).join(" ")}
      >
        {showComparisons ? (
          <colgroup>
            <col className="col-month" />
            <col className="col-forecast" />
            <col className="col-change-overall" />
            <col className="col-change-same" />
          </colgroup>
        ) : null}
        <thead>
          <tr>
            <th>Month</th>
            <th>Forecast Amount</th>
            {showComparisons ? <th>Change vs Overall History</th> : null}
            {showComparisons ? <th>Change vs Same Month History</th> : null}
            {showBounds ? <th>Lower</th> : null}
            {showBounds ? <th>Upper</th> : null}
            {showStatus ? <th>Status</th> : null}
          </tr>
        </thead>
        <tbody>
          {monthlyForecasts.map((row) => {
            const overall = changeVsHistory(row.forecast_amount, historicalAverage);
            const specificCount = Number(row.specific_month_history_count);
            const specificAvailable =
              Number.isFinite(Number(row.specific_month_historical_average)) && specificCount > 0;
            const specific = changeVsHistory(row.forecast_amount, row.specific_month_historical_average);
            const overallMeta = changeMeta(overall.amount, overall.percent, amountUnit);
            const specificMeta = specificAvailable
              ? {
                  ...changeMeta(specific.amount, specific.percent, amountUnit),
                  title: specificMonthTitle(row, amountUnit),
                }
              : changeMeta(null, null, amountUnit, SPECIFIC_MONTH_UNAVAILABLE);
            return (
              <tr key={row.month}>
                <td>{isoToLabel(row.month)}</td>
                <td>{formatAmount(row.forecast_amount, amountUnit)}</td>
                {showComparisons ? <ChangeCell meta={overallMeta} /> : null}
                {showComparisons ? <ChangeCell meta={specificMeta} /> : null}
                {showBounds ? <td className="bound-cell">{formatBoundAmount(row.lower_bound, amountUnit)}</td> : null}
                {showBounds ? <td className="bound-cell">{formatBoundAmount(row.upper_bound, amountUnit)}</td> : null}
                {showStatus ? (
                  <td>
                    <span className="badge">Forecast</span>
                  </td>
                ) : null}
              </tr>
            );
          })}
          <tr className="total-row">
            <td>Forecasted Total</td>
            <td>{formatAmount(overallTotal, amountUnit)}</td>
            {showComparisons ? <td>—</td> : null}
            {showComparisons ? <td>—</td> : null}
            {showBounds ? <td>—</td> : null}
            {showBounds ? <td>—</td> : null}
            {showStatus ? <td>—</td> : null}
          </tr>
        </tbody>
      </table>
      </div>
    </div>
  );
}
