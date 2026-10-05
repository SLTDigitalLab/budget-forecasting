import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AnalyticsSectionHeading from "../components/AnalyticsSectionHeading";
import BudgetTrendAnalysis from "../components/BudgetTrendAnalysis";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import ExpectedRangeLabel, {
  EXPECTED_RANGE_LABEL,
  EXPECTED_RANGE_UNAVAILABLE,
} from "../components/ExpectedRangeLabel";
import ForecastComparison from "../components/ForecastComparison";
import LoadingState from "../components/LoadingState";
import { getLatestForecastRecord, getOverviewAnalysis, getOverviewCategories } from "../api/forecastApi";
import { useForecast } from "../context/ForecastContext";
import { resolveAnalyticsForecast } from "../utils/analyticsForecast";
import {
  analyticsPageMode,
  codeHistoryRanking,
  historyRangeLabel,
  shortHistoryLabel,
  validateHistoricalAnalysis,
} from "../utils/analyticsPage";
import { buildBudgetTrend } from "../utils/budgetTrend";
import { buildForecastComparison, mergeHistoricalActuals } from "../utils/forecastComparison";
import { cartesianChartMargin, valueYAxisProps } from "../utils/chartLayout";
import {
  axisUnitLabel,
  chronologicalForecasts,
  formatAmount,
  formatAxisTick,
  formatPercentChange,
  periodLabel,
} from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";

const DEFAULT_CATEGORY_ID = "international_settlement";

function monthTitle(isoMonth) {
  return isoToLabel(isoMonth || "").replace("-", " ");
}

function budgetCodesFromForecast(forecast) {
  const rows = Array.isArray(forecast?.account_monthly_forecasts) ? forecast.account_monthly_forecasts : [];
  const seen = new Map();
  rows.forEach((row) => {
    const code = String(row.budget_code || "");
    if (!code || seen.has(code)) {
      return;
    }
    seen.set(code, {
      budget_code: code,
      account_name: row.account_name || "",
    });
  });
  return Array.from(seen.values());
}

function yearlyActuals(historical) {
  const totals = new Map();
  (Array.isArray(historical) ? historical : []).forEach((row) => {
    if (!row?.month || !Number.isFinite(Number(row.actual_amount))) {
      return;
    }
    const year = Number(String(row.month).slice(0, 4));
    const current = totals.get(year) || { total: 0, months: 0 };
    current.total += Number(row.actual_amount);
    current.months += 1;
    totals.set(year, current);
  });
  return totals;
}

function codeRanking(forecast) {
  const totals = new Map();
  (Array.isArray(forecast?.account_monthly_forecasts) ? forecast.account_monthly_forecasts : []).forEach((row) => {
    const code = String(row.budget_code || "");
    if (!code || !Number.isFinite(Number(row.forecast_amount))) {
      return;
    }
    const current = totals.get(code) || {
      budget_code: code,
      account_name: row.account_name || "",
      total: 0,
    };
    current.total += Number(row.forecast_amount);
    if (row.account_name) {
      current.account_name = row.account_name;
    }
    totals.set(code, current);
  });
  const rows = Array.from(totals.values()).sort((left, right) => right.total - left.total);
  const grand = rows.reduce((sum, row) => sum + row.total, 0);
  return rows.map((row, index) => ({
    ...row,
    rank: index + 1,
    share: grand === 0 ? 0 : row.total / grand,
  }));
}

function growthClass(value) {
  if (!Number.isFinite(value)) {
    return "";
  }
  if (value > 0) {
    return "analytics-growth-up";
  }
  if (value < 0) {
    return "analytics-growth-down";
  }
  return "";
}

function ChartTooltip({ children }) {
  return <div className="chart-tooltip">{children}</div>;
}

function SegControl({ id, label, value, options, onChange }) {
  return (
    <div className="analytics-seg" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          id={option.value === value ? id : undefined}
          type="button"
          className={option.value === value ? "active" : ""}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function ForecastAnalysisPanel({ forecast }) {
  const [scope, setScope] = useState("combined");
  const [grain, setGrain] = useState("monthly");
  const [budgetCode, setBudgetCode] = useState("");

  const codes = useMemo(() => budgetCodesFromForecast(forecast), [forecast]);
  const ranking = useMemo(() => codeRanking(forecast), [forecast]);

  useEffect(() => {
    if (!codes.length) {
      setBudgetCode("");
      return;
    }
    if (!codes.some((item) => item.budget_code === budgetCode)) {
      setBudgetCode(codes[0].budget_code);
    }
  }, [budgetCode, codes]);

  const amountUnit = forecast?.amount_unit;
  const unit = axisUnitLabel(amountUnit);
  const selectedCode = codes.find((item) => item.budget_code === budgetCode);
  const historyStart = forecast?.historical_start || forecast?.historical_actuals?.[0]?.month;
  const historyEnd = forecast?.history_end || forecast?.historical_end;
  const mixedYears = useMemo(() => {
    const actuals = yearlyActuals(forecast.historical_actuals);
    const forecasts = Array.isArray(forecast.combined_yearly_forecasts)
      ? forecast.combined_yearly_forecasts
      : [];
    const years = new Set([...actuals.keys(), ...forecasts.map((row) => Number(row.year))]);
    return Array.from(years)
      .filter((year) => Number.isFinite(year))
      .sort((left, right) => left - right)
      .map((year) => {
        const forecastRow = forecasts.find((row) => Number(row.year) === year);
        const actualRow = actuals.get(year);
        return {
          year,
          actual: actualRow ? actualRow.total : null,
          forecast: forecastRow ? Number(forecastRow.forecast_amount) : null,
        };
      })
      .filter((row) => row.actual != null && row.forecast != null);
  }, [forecast]);

  const combinedMonthly = useMemo(() => {
    const historical = (Array.isArray(forecast.historical_actuals) ? forecast.historical_actuals : [])
      .filter((row) => row?.month && Number.isFinite(Number(row.actual_amount)))
      .sort((left, right) => String(left.month).localeCompare(String(right.month)))
      .slice(-12);
    const forecasts = chronologicalForecasts(forecast);
    const byMonth = new Map();
    historical.forEach((row) => {
      byMonth.set(row.month, {
        month: row.month,
        label: isoToLabel(row.month),
        actual: Number(row.actual_amount),
        forecast: null,
      });
    });
    forecasts.forEach((row) => {
      const existing = byMonth.get(row.month) || {
        month: row.month,
        label: isoToLabel(row.month),
        actual: null,
        forecast: null,
      };
      existing.forecast = Number(row.forecast_amount);
      existing.lower = Number.isFinite(Number(row.lower_bound)) ? Number(row.lower_bound) : null;
      existing.upper = Number.isFinite(Number(row.upper_bound)) ? Number(row.upper_bound) : null;
      existing.intervalStatus = row.interval_status || null;
      byMonth.set(row.month, existing);
    });
    return Array.from(byMonth.values()).sort((left, right) => left.month.localeCompare(right.month));
  }, [forecast]);

  const combinedYearly = useMemo(() => {
    const actuals = yearlyActuals(forecast.historical_actuals);
    const forecasts = Array.isArray(forecast.combined_yearly_forecasts)
      ? forecast.combined_yearly_forecasts
      : [];
    const years = new Set([...actuals.keys(), ...forecasts.map((row) => Number(row.year))]);
    return Array.from(years)
      .filter((year) => Number.isFinite(year))
      .sort((left, right) => left - right)
      .map((year) => {
        const forecastRow = forecasts.find((row) => Number(row.year) === year);
        const actualRow = actuals.get(year);
        return {
          year,
          label: (actualRow && actualRow.months < 12) || forecastRow?.year_status === "PARTIAL_YEAR"
            ? `${year}*`
            : String(year),
          actual: actualRow ? actualRow.total : null,
          actual_months: actualRow ? actualRow.months : 0,
          forecast: forecastRow ? Number(forecastRow.forecast_amount) : null,
          year_status: forecastRow?.year_status || "",
          months_included: forecastRow?.months_included || 0,
        };
      });
  }, [forecast]);

  const codeMonthly = useMemo(() => {
    return (Array.isArray(forecast?.account_monthly_forecasts) ? forecast.account_monthly_forecasts : [])
      .filter((row) => row.budget_code === budgetCode)
      .sort((left, right) => String(left.month).localeCompare(String(right.month)))
      .map((row) => ({
        month: row.month,
        label: isoToLabel(row.month),
        forecast: Number(row.forecast_amount),
        lower: Number.isFinite(Number(row.lower_bound)) ? Number(row.lower_bound) : null,
        upper: Number.isFinite(Number(row.upper_bound)) ? Number(row.upper_bound) : null,
        intervalStatus: row.interval_status || null,
      }));
  }, [budgetCode, forecast]);

  const codeYearly = useMemo(() => {
    return (Array.isArray(forecast?.account_yearly_forecasts) ? forecast.account_yearly_forecasts : [])
      .filter((row) => row.budget_code === budgetCode)
      .sort((left, right) => Number(left.year) - Number(right.year))
      .map((row) => ({
        year: row.year,
        label: row.year_status === "PARTIAL_YEAR" ? `${row.year}*` : String(row.year),
        forecast: Number(row.forecast_amount),
        year_status: row.year_status,
        months_included: row.months_included,
      }));
  }, [budgetCode, forecast]);

  const chartRows = scope === "combined"
    ? (grain === "monthly" ? combinedMonthly : combinedYearly)
    : (grain === "monthly" ? codeMonthly : codeYearly);
  const showActuals = scope === "combined";
  const showInterval = grain === "monthly" && chartRows.some(
    (row) => Number.isFinite(Number(row.lower)) && Number.isFinite(Number(row.upper))
  );
  const chartTitle = scope === "combined"
    ? (grain === "monthly" ? "Combined monthly trend" : "Combined yearly comparison")
    : (grain === "monthly" ? `${budgetCode} monthly forecast` : `${budgetCode} yearly forecast`);

  function selectCode(code) {
    setBudgetCode(code);
    setScope("code");
  }

  return (
    <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-forecast-heading">
      <AnalyticsSectionHeading id="analytics-forecast-heading" index="04" title="Forecast Analysis" />
      <section className="analytics-period-bar" aria-label="History and forecast periods">
        <div>
          <span>History</span>
          <strong>{`${monthTitle(historyStart)} – ${monthTitle(historyEnd)}`}</strong>
          <em>Actuals only</em>
        </div>
        <div>
          <span>Forecast</span>
          <strong>{periodLabel(forecast)}</strong>
          <em>Generated period</em>
        </div>
        {mixedYears.length ? (
          <p className="analytics-partial-note">
            {mixedYears.map((row) => row.year).join(", ")}
            {mixedYears.length === 1 ? " contains" : " contain"}
            {" partial actuals and a partial forecast. They are not full-year totals."}
          </p>
        ) : null}
      </section>

      <ul className="analytics-kpis" aria-label="Forecast snapshot">
        <li>
          <span>Forecast total</span>
          <strong>{formatAmount(forecast.overall_total, amountUnit)}</strong>
        </li>
        <li>
          <span>Monthly average</span>
          <strong>{formatAmount(forecast.monthly_average, amountUnit)}</strong>
        </li>
        <li>
          <span>Months</span>
          <strong>{forecast.forecast_month_count || chronologicalForecasts(forecast).length}</strong>
        </li>
        <li>
          <span>Budget Codes</span>
          <strong>{forecast.selected_account_count || ranking.length}</strong>
        </li>
      </ul>

      <div className="analytics-board">
        <article className="card analytics-main">
          <div className="analytics-main-head">
            <div>
              <h3>{chartTitle}</h3>
              <p className="muted">
                {showActuals
                  ? grain === "monthly"
                    ? "Last 12 actual months plus the generated forecast. Series do not overlap."
                    : "Actual and forecast bars in the same year are different month ranges."
                  : selectedCode?.account_name
                    ? `${selectedCode.account_name}. Combined historical actuals are not split by Budget Code.`
                    : "Combined historical actuals are not split by Budget Code."}
              </p>
              {showInterval ? <ExpectedRangeLabel /> : null}
            </div>
            <div className="analytics-filters">
              <SegControl
                id="analytics-scope"
                label="Analysis"
                value={scope}
                onChange={setScope}
                options={[
                  { value: "combined", label: "Combined" },
                  { value: "code", label: "Budget Code" },
                ]}
              />
              <SegControl
                id="analytics-grain"
                label="View"
                value={grain}
                onChange={setGrain}
                options={[
                  { value: "monthly", label: "Monthly" },
                  { value: "yearly", label: "Yearly" },
                ]}
              />
              {scope === "code" ? (
                <label className="analytics-code-field">
                  <span className="sr-only">Budget Code</span>
                  <select
                    id="analytics-code"
                    value={budgetCode}
                    onChange={(event) => setBudgetCode(event.target.value)}
                  >
                    {codes.map((item) => (
                      <option key={item.budget_code} value={item.budget_code}>
                        {item.budget_code}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
          </div>
          {!chartRows.length ? (
            <EmptyState message="No forecast points are available for this view." />
          ) : (
            <div className="analytics-chart" role="img" aria-label={chartTitle}>
              <ResponsiveContainer width="100%" height="100%">
                {grain === "monthly" ? (
                  <ComposedChart data={chartRows} margin={cartesianChartMargin()}>
                    <defs>
                      <linearGradient id="forecastAnalysisIntervalFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#1f7ae0" stopOpacity={0.18} />
                        <stop offset="95%" stopColor="#1f7ae0" stopOpacity={0.04} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="#e6eef6" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} tickMargin={6} />
                    <YAxis {...valueYAxisProps(unit)} tickFormatter={formatAxisTick} />
                    <Tooltip
                      allowEscapeViewBox={{ x: true, y: true }}
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none" }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) {
                          return null;
                        }
                        const point = payload[0].payload;
                        return (
                          <ChartTooltip>
                            <div className="chart-tooltip-month">{monthTitle(point.month)}</div>
                            {point.actual != null ? (
                              <div className="chart-tooltip-value">{`Actual ${formatAmount(point.actual, amountUnit)}`}</div>
                            ) : null}
                            {point.forecast != null ? (
                              <div className="chart-tooltip-value">{`Forecast ${formatAmount(point.forecast, amountUnit)}`}</div>
                            ) : null}
                            {Number.isFinite(Number(point.lower)) && Number.isFinite(Number(point.upper)) ? (
                              <div className="chart-tooltip-value">
                                {`${EXPECTED_RANGE_LABEL} ${formatAmount(point.lower, amountUnit)} – ${formatAmount(point.upper, amountUnit)}`}
                              </div>
                            ) : point.intervalStatus === "unavailable_beyond_calibrated_horizon" ? (
                              <div className="chart-tooltip-value">{EXPECTED_RANGE_UNAVAILABLE}</div>
                            ) : null}
                          </ChartTooltip>
                        );
                      }}
                    />
                    {showActuals ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null}
                    {showInterval ? (
                      <Area
                        type="linear"
                        dataKey="upper"
                        name="Upper interval"
                        stroke="none"
                        fill="url(#forecastAnalysisIntervalFill)"
                        legendType="none"
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
                        legendType="none"
                        isAnimationActive={false}
                        connectNulls={false}
                      />
                    ) : null}
                    {showActuals ? (
                      <Line
                        type="monotone"
                        dataKey="actual"
                        name="Historical actual"
                        stroke="#071a33"
                        strokeWidth={2.2}
                        dot={false}
                        connectNulls={false}
                      />
                    ) : null}
                    <Line
                      type="monotone"
                      dataKey="forecast"
                      name="Forecast"
                      stroke="#1f7ae0"
                      strokeWidth={2.4}
                      dot={false}
                      connectNulls={false}
                    />
                  </ComposedChart>
                ) : (
                  <BarChart data={chartRows} margin={cartesianChartMargin()}>
                    <CartesianGrid stroke="#e6eef6" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} tickMargin={6} />
                    <YAxis {...valueYAxisProps(unit)} tickFormatter={formatAxisTick} />
                    <Tooltip
                      allowEscapeViewBox={{ x: true, y: true }}
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none" }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) {
                          return null;
                        }
                        const point = payload[0].payload;
                        return (
                          <ChartTooltip>
                            <div className="chart-tooltip-month">{point.year}</div>
                            {point.actual != null ? (
                              <div className="chart-tooltip-value">
                                {`Actual ${formatAmount(point.actual, amountUnit)}${point.actual_months && point.actual_months < 12 ? ` · ${point.actual_months} months` : ""}`}
                              </div>
                            ) : null}
                            {point.forecast != null ? (
                              <div className="chart-tooltip-value">
                                {`Forecast ${formatAmount(point.forecast, amountUnit)}${point.year_status === "PARTIAL_YEAR" ? ` · ${point.months_included} months` : ""}`}
                              </div>
                            ) : null}
                          </ChartTooltip>
                        );
                      }}
                    />
                    {showActuals ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null}
                    {showActuals ? (
                      <Bar dataKey="actual" name="Historical actual" fill="#071a33" radius={[4, 4, 0, 0]} maxBarSize={22} />
                    ) : null}
                    <Bar dataKey="forecast" name="Forecast" fill="#1f7ae0" radius={[4, 4, 0, 0]} maxBarSize={22} />
                  </BarChart>
                )}
              </ResponsiveContainer>
            </div>
          )}
        </article>

        <article className="card analytics-rank">
          <h3>Budget Code ranking</h3>
          <p className="muted">Share of the generated forecast total. Select a code to inspect it.</p>
          <div className="data-scroll" tabIndex={0} aria-label="Scrollable Budget Code ranking">
          <ol className="analytics-rank-list">
            {ranking.map((row) => (
              <li key={row.budget_code}>
                <button
                  type="button"
                  className={row.budget_code === budgetCode && scope === "code" ? "active" : ""}
                  onClick={() => selectCode(row.budget_code)}
                >
                  <span className="rank-index">{row.rank}</span>
                  <span className="rank-copy">
                    <strong>{row.budget_code}</strong>
                    <em title={row.account_name}>{row.account_name || "—"}</em>
                  </span>
                  <span className="rank-meter" aria-hidden="true">
                    <i style={{ width: `${Math.max(row.share * 100, 2)}%` }} />
                  </span>
                  <span className="rank-stats">
                    <strong>{formatAmount(row.total, amountUnit)}</strong>
                    <em>{formatPercentChange(row.share * 100).replace("+", "")}</em>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          </div>
        </article>
      </div>
    </section>
  );
}

export default function Analytics() {
  const { forecast, error: forecastContextError, applyForecast } = useForecast();
  const [categories, setCategories] = useState([]);
  const [categoryId, setCategoryId] = useState("");
  const [history, setHistory] = useState(null);
  const [historyStatus, setHistoryStatus] = useState("loading");
  const [historyError, setHistoryError] = useState("");
  const [restoreStatus, setRestoreStatus] = useState(forecast ? "ready" : "loading");
  const [restoreError, setRestoreError] = useState("");

  const loadHistory = useCallback(async (requestedCategory) => {
    setHistoryStatus("loading");
    setHistoryError("");
    try {
      const payload = await getOverviewAnalysis(requestedCategory);
      const invalid = validateHistoricalAnalysis(payload);
      if (invalid) {
        throw new Error(invalid);
      }
      setHistory(payload);
      setCategoryId(payload.category.id);
      setHistoryStatus("ready");
    } catch (cause) {
      const message = cause.message || "Unable to load historical analysis.";
      if (/no historical actuals/i.test(message)) {
        setHistory(null);
        setHistoryStatus("empty");
        setHistoryError(message);
        return;
      }
      setHistory(null);
      setHistoryStatus("error");
      setHistoryError(message);
    }
  }, []);

  const restoreLatest = useCallback(async () => {
    if (forecast) {
      setRestoreStatus("ready");
      setRestoreError("");
      return;
    }
    setRestoreStatus("loading");
    setRestoreError("");
    try {
      const result = await resolveAnalyticsForecast({
        currentForecast: forecast,
        fetchLatest: getLatestForecastRecord,
      });
      if (result.status === "history") {
        applyForecast(result.forecast);
        setRestoreStatus("ready");
        return;
      }
      setRestoreStatus(result.status === "empty" ? "empty" : "ready");
    } catch (cause) {
      setRestoreStatus("error");
      setRestoreError(cause.message || "Unable to load the latest forecast.");
    }
  }, [applyForecast, forecast]);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      setHistoryStatus("loading");
      try {
        const catalog = await getOverviewCategories();
        if (cancelled) {
          return;
        }
        const items = catalog.categories || [];
        setCategories(items);
        const selected =
          items.find((item) => item.id === (catalog.default_category || DEFAULT_CATEGORY_ID)) || items[0];
        if (!selected) {
          setHistoryStatus("empty");
          setHistoryError("No categories were found in the historical data.");
          return;
        }
        setCategoryId(selected.id);
        await loadHistory(selected.id);
      } catch (cause) {
        if (!cancelled) {
          setHistoryStatus("error");
          setHistoryError(cause.message || "Unable to load Analytics categories.");
        }
      }
    }
    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadHistory]);

  useEffect(() => {
    restoreLatest();
  }, [restoreLatest]);

  const mode = analyticsPageMode({ historyStatus, history, forecast });
  const amountUnit = history?.amount_unit;
  const unit = axisUnitLabel(amountUnit);
  const ranking = useMemo(() => codeHistoryRanking(history?.budget_codes), [history]);
  const monthlyTrend = useMemo(
    () => (Array.isArray(history?.monthly_actuals) ? history.monthly_actuals : []).map((row) => ({
      ...row,
      label: shortHistoryLabel(row.month),
      actual: row.actual_amount == null ? null : Number(row.actual_amount),
    })),
    [history]
  );
  const yearlyTrend = useMemo(
    () => (Array.isArray(history?.yearly_actuals) ? history.yearly_actuals : []).map((row) => ({
      ...row,
      label: row.year_status === "PARTIAL_YEAR" ? `${row.year}*` : String(row.year),
      actual: Number(row.actual_amount),
    })),
    [history]
  );
  const seasonalTrend = useMemo(
    () => (Array.isArray(history?.seasonal_profile) ? history.seasonal_profile : []).map((row) => ({
      ...row,
      average: row.average_amount == null ? null : Number(row.average_amount),
    })),
    [history]
  );
  const partialYears = yearlyTrend.filter((row) => row.year_status === "PARTIAL_YEAR");
  const comparisonRows = useMemo(() => {
    if (!forecast) {
      return [];
    }
    return buildForecastComparison({
      historicalActuals: mergeHistoricalActuals(history?.monthly_actuals, forecast.historical_actuals),
      forecast,
      historyEnd: history?.history_end || history?.latest_actual_month || forecast.history_end || forecast.historical_end,
    });
  }, [forecast, history]);
  const budgetTrend = useMemo(() => {
    if (!forecast) {
      return null;
    }
    return buildBudgetTrend({
      monthlyActuals: history?.monthly_actuals,
      forecast,
    });
  }, [forecast, history]);

  return (
    <section className="analytics-page">
      <header className="page-header">
        <div>
          <h1>Analytics & Charts</h1>
        </div>
      </header>

      <section className="analytics-major-section" aria-labelledby="analytics-historical-heading">
        <AnalyticsSectionHeading
          id="analytics-historical-heading"
          index="01"
          title="Historical Budget Analysis"
        />

      {mode.showHistoricalError ? (
        <div className="card overview-inline-error" role="alert">
          <p>{historyError}</p>
          <button className="generate-button" type="button" onClick={() => loadHistory(categoryId)}>
            Retry
          </button>
        </div>
      ) : null}

      {mode.showHistoricalLoading ? (
        <article className="card">
          <LoadingState label="Loading historical analysis..." />
        </article>
      ) : null}

      {mode.showHistoricalEmpty ? (
        <article className="card empty-panel">
          <EmptyState message={historyError || "No stored historical actuals are available for analysis."} />
        </article>
      ) : null}

      {mode.showHistorical ? (
        <>
          <section className="analytics-period-bar" aria-label="Historical analysis scope">
            <div>
              <span>History</span>
              <strong>{historyRangeLabel(history.history_start, history.history_end)}</strong>
              <em>Stored actuals</em>
            </div>
            <div>
              <span>Category</span>
              <label className="analytics-code-field">
                <span className="sr-only">Category</span>
                <select
                  id="analytics-category"
                  value={categoryId}
                  onChange={(event) => {
                    setCategoryId(event.target.value);
                    loadHistory(event.target.value);
                  }}
                  disabled={!categories.length}
                >
                  {categories.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <em>Actuals only</em>
            </div>
            {partialYears.length ? (
              <p className="analytics-partial-note">
                {partialYears.map((row) => row.year).join(", ")}
                {partialYears.length === 1 ? " is a partial year." : " are partial years."}
                {" Year-on-year growth compares overlapping months only."}
              </p>
            ) : null}
          </section>

          <ul className="analytics-kpis" aria-label="Historical snapshot">
            <li>
              <span>Total actuals</span>
              <strong>{formatAmount(history.overall_total, amountUnit)}</strong>
            </li>
            <li>
              <span>Monthly average</span>
              <strong>{formatAmount(history.monthly_average, amountUnit)}</strong>
            </li>
            <li>
              <span>Latest YoY growth</span>
              <strong className={growthClass(history.latest_year_growth_percent)}>
                {formatPercentChange(history.latest_year_growth_percent)}
              </strong>
            </li>
            <li>
              <span>Recurring codes</span>
              <strong>{`${history.recurring_count || 0} / ${history.budget_code_count || ranking.length}`}</strong>
            </li>
          </ul>

          <div className="analytics-history-grid">
            <article className="card analytics-main analytics-span">
              <div className="analytics-main-head">
                <div>
                  <h2>Expenditure trend</h2>
                  <p className="muted">Combined monthly actuals across the stored history period.</p>
                </div>
              </div>
              <div className="analytics-chart analytics-chart-wide" role="img" aria-label="Expenditure trend">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={monthlyTrend} margin={cartesianChartMargin()}>
                    <CartesianGrid stroke="#e6eef6" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} minTickGap={22} tickMargin={6} />
                    <YAxis {...valueYAxisProps(unit)} tickFormatter={formatAxisTick} />
                    <Tooltip
                      allowEscapeViewBox={{ x: true, y: true }}
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none" }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) {
                          return null;
                        }
                        const point = payload[0].payload;
                        return (
                          <ChartTooltip>
                            <div className="chart-tooltip-month">{monthTitle(point.month)}</div>
                            <div className="chart-tooltip-value">
                              {point.actual == null ? "No actual available" : formatAmount(point.actual, amountUnit)}
                            </div>
                          </ChartTooltip>
                        );
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="actual"
                      name="Historical actual"
                      stroke="#1f7ae0"
                      strokeWidth={2.2}
                      dot={false}
                      connectNulls={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </article>

            <article className="card analytics-main">
              <div className="analytics-main-head">
                <div>
                  <h2>Seasonal variation</h2>
                  <p className="muted">Average actuals by calendar month across available years.</p>
                </div>
              </div>
              <div className="analytics-chart" role="img" aria-label="Seasonal variation">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={seasonalTrend} margin={cartesianChartMargin()}>
                    <CartesianGrid stroke="#e6eef6" vertical={false} />
                    <XAxis dataKey="month_label" tick={{ fontSize: 11 }} tickMargin={6} />
                    <YAxis {...valueYAxisProps(unit)} tickFormatter={formatAxisTick} />
                    <Tooltip
                      allowEscapeViewBox={{ x: true, y: true }}
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none" }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) {
                          return null;
                        }
                        const point = payload[0].payload;
                        return (
                          <ChartTooltip>
                            <div className="chart-tooltip-month">{point.month_label}</div>
                            <div className="chart-tooltip-value">
                              {point.average == null ? "No seasonal average" : formatAmount(point.average, amountUnit)}
                            </div>
                            <div className="chart-tooltip-series">{`${point.observation_count} observation${point.observation_count === 1 ? "" : "s"}`}</div>
                          </ChartTooltip>
                        );
                      }}
                    />
                    <Bar dataKey="average" name="Average actual" fill="#1f7ae0" radius={[4, 4, 0, 0]} maxBarSize={22} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </article>

            <article className="card analytics-main">
              <div className="analytics-main-head">
                <div>
                  <h2>Yearly growth</h2>
                  <p className="muted">Annual totals with like-for-like growth against overlapping months.</p>
                </div>
              </div>
              <div className="analytics-chart" role="img" aria-label="Yearly growth">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={yearlyTrend} margin={cartesianChartMargin()}>
                    <CartesianGrid stroke="#e6eef6" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} tickMargin={6} />
                    <YAxis {...valueYAxisProps(unit)} tickFormatter={formatAxisTick} />
                    <Tooltip
                      allowEscapeViewBox={{ x: true, y: true }}
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none" }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) {
                          return null;
                        }
                        const point = payload[0].payload;
                        return (
                          <ChartTooltip>
                            <div className="chart-tooltip-month">{point.year}</div>
                            <div className="chart-tooltip-value">{formatAmount(point.actual, amountUnit)}</div>
                            <div className="chart-tooltip-series">
                              {point.year_status === "PARTIAL_YEAR"
                                ? `${point.months_included} months · ${formatPercentChange(point.growth_percent)} YoY`
                                : formatPercentChange(point.growth_percent)}
                            </div>
                          </ChartTooltip>
                        );
                      }}
                    />
                    <Bar dataKey="actual" name="Annual actual" fill="#071a33" radius={[4, 4, 0, 0]} maxBarSize={28} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </article>

            <article className="card analytics-rank analytics-span">
              <h2>Recurring expenses</h2>
              <p className="muted">
                Budget Codes ranked by historical actual total. Recurring codes appear in at least 75% of history months.
              </p>
              <div className="data-scroll" tabIndex={0} aria-label="Scrollable recurring expense Budget Codes">
              <ol className="analytics-rank-list">
                {ranking.map((row) => (
                  <li key={row.budget_code}>
                    <div className="analytics-rank-row">
                      <span className="rank-index">{row.rank}</span>
                      <span className="rank-copy">
                        <strong>
                          {row.budget_code}
                          {row.recurring ? <span className="analytics-recurring">Recurring</span> : null}
                        </strong>
                        <em title={row.account_name}>{row.account_name || "—"}</em>
                      </span>
                      <span className="rank-meter" aria-hidden="true">
                        <i style={{ width: `${Math.max(row.share * 100, 2)}%` }} />
                      </span>
                      <span className="rank-stats">
                        <strong>{formatAmount(row.actual_amount, amountUnit)}</strong>
                        <em>{`${row.months_present || 0}/${row.history_month_count || 0} months`}</em>
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
              </div>
            </article>
          </div>
        </>
      ) : null}
      </section>

      {mode.showBudgetTrend ? (
        <BudgetTrendAnalysis
          trend={budgetTrend}
          amountUnit={forecast?.amount_unit || history?.amount_unit}
        />
      ) : null}

      {mode.showComparison ? (
        <ForecastComparison
          rows={comparisonRows}
          amountUnit={forecast?.amount_unit || history?.amount_unit}
        />
      ) : null}

      {restoreStatus === "error" ? (
        <div className="card overview-inline-error" role="alert">
          <p>{restoreError}</p>
          <button className="generate-button" type="button" onClick={restoreLatest}>
            Retry forecast
          </button>
        </div>
      ) : forecastContextError && !forecast ? (
        <ErrorState message={forecastContextError} />
      ) : null}

      {restoreStatus === "loading" && !forecast && !mode.showHistoricalLoading ? (
        <article className="card">
          <LoadingState label="Checking saved forecast..." />
        </article>
      ) : mode.showForecastAnalysis ? (
        <ForecastAnalysisPanel forecast={forecast} />
      ) : (restoreStatus === "empty" || restoreStatus === "ready") && !mode.showHistoricalLoading ? (
        <section className="analytics-major-section" aria-labelledby="analytics-forecast-heading">
          <AnalyticsSectionHeading id="analytics-forecast-heading" index="04" title="Forecast Analysis" />
          <article className="card empty-panel analytics-forecast-optional">
            <EmptyState message="Forecast Analysis is available after a forecast is generated." />
            <Link className="generate-button overview-link" to="/generate-forecast">
              Go to Generate Forecast
            </Link>
          </article>
        </section>
      ) : null}
    </section>
  );
}
