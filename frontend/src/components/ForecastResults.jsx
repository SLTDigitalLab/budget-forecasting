import { useEffect, useState } from "react";
import BudgetCodeForecastTable from "./BudgetCodeForecastTable";
import ExpectedRangeLabel from "./ExpectedRangeLabel";
import ForecastSummary from "./ForecastSummary";
import ForecastTable from "./ForecastTable";
import MonthlyForecastChart from "./MonthlyForecastChart";
import { useForecast } from "../context/ForecastContext";
import {
  chronologicalForecasts,
  isPresentAmount,
  periodLabel,
} from "../utils/forecastDisplay";
import {
  changeVsHistory,
  historicalMonthlyBaseline,
  specificMonthHistoricalBaseline,
} from "../utils/forecastHistoryBaseline";
import { isoToLabel } from "../utils/months";

export default function ForecastResults() {
  const { forecast, loading } = useForecast();
  const [showBudgetCodes, setShowBudgetCodes] = useState(false);
  const forecastKey = [
    forecast?.generated_at,
    forecast?.requested_start_month,
    forecast?.requested_end_month,
  ].join("|");

  useEffect(() => {
    setShowBudgetCodes(false);
  }, [forecastKey]);

  if (!forecast) {
    return null;
  }
  const rows = chronologicalForecasts(forecast);
  const unit = forecast?.amount_unit || "LKR";
  const baseline = historicalMonthlyBaseline(forecast);
  const detailRows = rows.map((row) => {
    const overallChange = changeVsHistory(row.forecast_amount, baseline.average);
    const specific = specificMonthHistoricalBaseline(forecast, row.month);
    const specificChange = changeVsHistory(row.forecast_amount, specific.average);
    return {
      ...row,
      overall_historical_average: baseline.average,
      historical_month_count: baseline.count,
      change_vs_overall_amount: overallChange.amount,
      change_vs_overall_percentage: overallChange.percent,
      specific_month_historical_average: specific.average,
      specific_month_history_count: specific.count,
      change_vs_specific_month_amount: specificChange.amount,
      change_vs_specific_month_percentage: specificChange.percent,
    };
  });
  const chartData = detailRows.map((row) => ({
    month: isoToLabel(row.month),
    isoMonth: row.month,
    amount: row.forecast_amount,
    lower: row.lower_bound,
    upper: row.upper_bound,
    intervalStatus: row.interval_status,
    historicalAverage: row.overall_historical_average,
    historicalMonthCount: row.historical_month_count,
    changeAmount: row.change_vs_overall_amount,
    changePercent: row.change_vs_overall_percentage,
    specificAverage: row.specific_month_historical_average,
    specificCount: row.specific_month_history_count,
    specificChangeAmount: row.change_vs_specific_month_amount,
    specificChangePercent: row.change_vs_specific_month_percentage,
  }));
  const showInterval = rows.some(
    (row) => isPresentAmount(row.lower_bound) && isPresentAmount(row.upper_bound)
  );

  return (
    <div className="generate-results">
      <ForecastSummary forecast={forecast} />
      <section className="charts charts-single">
        <article className="card section-card panel generate-result-card">
          <h2 className="section-card-header">Combined Monthly Forecast</h2>
          <div className="section-card-body">
            <p className="muted">{periodLabel(forecast)}</p>
            {showInterval ? <ExpectedRangeLabel /> : null}
            <MonthlyForecastChart data={chartData} loading={loading} amountUnit={unit} />
          </div>
        </article>
      </section>
      <section className="card section-card table-card generate-result-card">
        <h2 className="section-card-header">Monthly Details</h2>
        <div className="section-card-body">
          <ForecastTable
            monthlyForecasts={detailRows}
            overallTotal={forecast.overall_total}
            amountUnit={unit}
            loading={loading}
            historicalAverage={baseline.average}
            historicalMonthCount={baseline.count}
            showHistoryComparisons
          />
        </div>
      </section>
      <BudgetCodeForecastTable
        forecast={forecast}
        amountUnit={unit}
        loading={loading}
        expanded={showBudgetCodes}
        onToggle={() => setShowBudgetCodes((open) => !open)}
      />
    </div>
  );
}
