import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ErrorBar,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AnalyticsSectionHeading from "../components/AnalyticsSectionHeading";
import BoundedChartTooltip from "../components/BoundedChartTooltip";
import BudgetTrendAnalysis from "../components/BudgetTrendAnalysis";
import ChartYearSummaries from "../components/ChartYearSummaries";
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
  ANALYTICS_TABS,
  actualDataCoverageLabel,
  analyticsPageMode,
  codeHistoryRanking,
  defaultAnalyticsTab,
  isAnalyticsTab,
  latestCompleteYearDisplay,
  latestSubmissionMeta,
  partialYearNote,
  shortHistoryLabel,
  validateHistoricalAnalysis,
} from "../utils/analyticsPage";
import { buildBudgetTrend } from "../utils/budgetTrend";
import { forecastMatchesOverviewCategory } from "../utils/forecastCategory";
import { buildForecastComparison, mergeHistoricalActuals } from "../utils/forecastComparison";
import {
  cartesianChartMargin,
  INTERVAL_BAR_STROKE,
  SOLID_BAR_RADIUS,
  valueYAxisProps,
  withIntervalError,
} from "../utils/chartLayout";
import {
  isYearTotalRow,
  summarizeDisplayedYears,
} from "../utils/chartYearTotals";
import {
  HISTORY_RANGE_ALL,
  HISTORY_RANGE_LATEST_2_YEARS,
  buildHistoricalDisplayAnalysis,
} from "../utils/historicalDisplayWindow";
import { readOverviewHistoryEpoch } from "../utils/overviewHistoryEpoch";
import useChartPalette from "../hooks/useChartPalette";
import {
  axisUnitLabel,
  chronologicalForecasts,
  formatAmount,
  formatAxisTick,
  formatPercentChange,
  periodLabel,
} from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";

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
  return <div className="chart-tooltip chart-tooltip-overlay">{children}</div>;
}

function HistoricalMonthlyTrendChart({ monthlyTrend, amountUnit, unit }) {
  const palette = useChartPalette();
  const hostRef = useRef(null);
  const [active, setActive] = useState(null);
  const chartRows = useMemo(
    () => (Array.isArray(monthlyTrend) ? monthlyTrend : []).filter((row) => !isYearTotalRow(row)),
    [monthlyTrend]
  );

  return (
    <div className="analytics-chart analytics-chart-wide analytics-chart-fit" role="img" aria-label="Expenditure trend" ref={hostRef}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={chartRows}
          margin={cartesianChartMargin({ bottom: 20, top: 8, right: 12 })}
          barCategoryGap="18%"
          onMouseMove={(state) => {
            if (!state?.isTooltipActive || !state.activePayload?.length || !state.activeCoordinate) {
              setActive(null);
              return;
            }
            setActive({
              point: state.activePayload[0].payload,
              x: state.activeCoordinate.x,
              y: state.activeCoordinate.y,
            });
          }}
          onMouseLeave={() => setActive(null)}
        >
          <CartesianGrid stroke={palette.grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: palette.tick }}
            minTickGap={22}
            tickMargin={8}
            interval="preserveStartEnd"
          />
          <YAxis {...valueYAxisProps(unit, { axisFill: palette.axis })} tickFormatter={formatAxisTick} />
          <Tooltip cursor={{ fill: palette.cursor }} content={() => null} wrapperStyle={{ display: "none" }} />
          <Bar dataKey="actual" name="Historical actual" fill={palette.historical} maxBarSize={22} radius={SOLID_BAR_RADIUS} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <BoundedChartTooltip active={Boolean(active)} anchor={active} hostRef={hostRef}>
        {active ? (
          <ChartTooltip>
            <div className="chart-tooltip-month">{monthTitle(active.point.month)}</div>
            <div className="chart-tooltip-value">
              {active.point.actual == null ? "No actual available" : formatAmount(active.point.actual, amountUnit)}
            </div>
          </ChartTooltip>
        ) : null}
      </BoundedChartTooltip>
    </div>
  );
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
  const palette = useChartPalette();
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

  const combinedMonthly = useMemo(() => {
    return chronologicalForecasts(forecast).map((row) => ({
      month: row.month,
      label: isoToLabel(row.month),
      forecast: Number(row.forecast_amount),
      lower: Number.isFinite(Number(row.lower_bound)) ? Number(row.lower_bound) : null,
      upper: Number.isFinite(Number(row.upper_bound)) ? Number(row.upper_bound) : null,
      intervalStatus: row.interval_status || null,
    }));
  }, [forecast]);

  const combinedYearly = useMemo(() => {
    const forecasts = Array.isArray(forecast.combined_yearly_forecasts)
      ? forecast.combined_yearly_forecasts
      : [];
    return forecasts
      .map((row) => ({
        year: Number(row.year),
        label: row.year_status === "PARTIAL_YEAR" ? `${row.year}*` : String(row.year),
        forecast: Number(row.forecast_amount),
        year_status: row.year_status || "",
        months_included: row.months_included || 0,
      }))
      .filter((row) => Number.isFinite(row.year) && Number.isFinite(row.forecast))
      .sort((left, right) => left.year - right.year);
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
  const monthlyChartRows = useMemo(
    () => (grain === "monthly" ? (Array.isArray(chartRows) ? chartRows : []).filter((row) => !isYearTotalRow(row)) : []),
    [chartRows, grain]
  );
  const monthlySummaries = useMemo(() => {
    if (grain !== "monthly") {
      return [];
    }
    return summarizeDisplayedYears(monthlyChartRows, {
      isoKey: "month",
      valueKey: "forecast",
      seriesKind: "forecast",
    });
  }, [grain, monthlyChartRows]);
  const showInterval = grain === "monthly" && monthlyChartRows.some(
    (row) => Number.isFinite(Number(row.lower)) && Number.isFinite(Number(row.upper))
  );
  const partialForecastYears = useMemo(
    () => combinedYearly.filter((row) => row.year_status === "PARTIAL_YEAR").map((row) => row.year),
    [combinedYearly]
  );
  const chartTitle = scope === "combined"
    ? (grain === "monthly" ? "Combined monthly forecast" : "Combined yearly forecast")
    : (grain === "monthly" ? `${budgetCode} monthly forecast` : `${budgetCode} yearly forecast`);

  function selectCode(code) {
    setBudgetCode(code);
    setScope("code");
  }

  return (
    <section className="analytics-major-section analytics-forecast-section" aria-labelledby="analytics-forecast-heading">
      <AnalyticsSectionHeading id="analytics-forecast-heading" title="Forecast Analysis" />
      <section className="analytics-meta-grid" aria-label="Active forecast">
        <div className="analytics-meta-item">
          <span>Forecast category</span>
          <strong>{forecast.category || "—"}</strong>
        </div>
        <div className="analytics-meta-item">
          <span>Forecast period</span>
          <strong>{periodLabel(forecast)}</strong>
        </div>
      </section>
      {partialForecastYears.length ? (
        <p className="analytics-partial-note">
          {partialForecastYears.join(", ")}
          {partialForecastYears.length === 1 ? " is a" : " are"}
          {" partial forecast year"}
          {partialForecastYears.length === 1 ? "" : "s"}
          {". Totals cover only included forecast months."}
        </p>
      ) : null}

      <ul className="analytics-kpis" aria-label="Forecast snapshot">
        <li>
          <span>Forecast total</span>
          <strong>{formatAmount(forecast.overall_total, amountUnit)}</strong>
        </li>
        <li>
          <span>Monthly average</span>
          <strong>{formatAmount(forecast.monthly_average, amountUnit)}</strong>
          <em className="analytics-kpi-note">Average across forecast months</em>
        </li>
        <li>
          <span>Forecast months</span>
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
                {scope === "combined"
                  ? grain === "monthly"
                    ? "Forecast values only for the active generated forecast. Historical actuals are shown on Comparison."
                    : "Yearly forecast totals for the active generated forecast only."
                  : selectedCode?.account_name
                    ? `${selectedCode.account_name}. Forecast values for the selected Budget Code.`
                    : "Forecast values for the selected Budget Code."}
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
          ) : grain === "monthly" ? (
            <div className="analytics-expenditure-panel">
              <ChartYearSummaries summaries={monthlySummaries} amountUnit={amountUnit} />
              <div className="analytics-chart analytics-chart-fit" role="img" aria-label={chartTitle}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={withIntervalError(monthlyChartRows, "forecast")}
                    margin={cartesianChartMargin({ bottom: 16, right: 12 })}
                    barGap={4}
                    barCategoryGap="18%"
                  >
                    <CartesianGrid stroke={palette.grid} vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: palette.tick }} tickMargin={8} minTickGap={18} interval="preserveStartEnd" />
                    <YAxis {...valueYAxisProps(unit, { axisFill: palette.axis })} tickFormatter={formatAxisTick} />
                    <Tooltip
                      cursor={{ fill: palette.cursor }}
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none", maxWidth: "min(320px, calc(100vw - 24px))" }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) {
                          return null;
                        }
                        const point = payload[0].payload;
                        const showRange =
                          point.forecast != null &&
                          Number.isFinite(Number(point.lower)) &&
                          Number.isFinite(Number(point.upper));
                        return (
                          <ChartTooltip>
                            <div className="chart-tooltip-month">{monthTitle(point.month)}</div>
                            {point.forecast != null ? (
                              <div className="chart-tooltip-value">{`Forecast ${formatAmount(point.forecast, amountUnit)}`}</div>
                            ) : null}
                            {showRange ? (
                              <div className="chart-tooltip-value">
                                {`${EXPECTED_RANGE_LABEL} ${formatAmount(point.lower, amountUnit)} – ${formatAmount(point.upper, amountUnit)}`}
                              </div>
                            ) : point.forecast != null && point.intervalStatus === "unavailable_beyond_calibrated_horizon" ? (
                              <div className="chart-tooltip-value">{EXPECTED_RANGE_UNAVAILABLE}</div>
                            ) : null}
                          </ChartTooltip>
                        );
                      }}
                    />
                    <Bar
                      dataKey="forecast"
                      name="Forecast"
                      fill={palette.forecast}
                      radius={SOLID_BAR_RADIUS}
                      maxBarSize={22}
                      isAnimationActive={false}
                    >
                      {showInterval ? (
                        <ErrorBar dataKey="intervalError" width={6} strokeWidth={1.25} stroke={palette.interval || INTERVAL_BAR_STROKE} direction="y" />
                      ) : null}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <div className="analytics-chart analytics-chart-fit" role="img" aria-label={chartTitle}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartRows} margin={cartesianChartMargin()}>
                  <CartesianGrid stroke={palette.grid} vertical={false} />
                  <XAxis dataKey="label" tick={{ fontSize: 11, fill: palette.tick }} tickMargin={6} />
                  <YAxis {...valueYAxisProps(unit, { axisFill: palette.axis })} tickFormatter={formatAxisTick} />
                  <Tooltip
                    wrapperStyle={{ zIndex: 5, pointerEvents: "none", maxWidth: "min(320px, calc(100vw - 24px))" }}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) {
                        return null;
                      }
                      const point = payload[0].payload;
                      return (
                        <ChartTooltip>
                          <div className="chart-tooltip-month">{point.year}</div>
                          {point.forecast != null ? (
                            <div className="chart-tooltip-value">
                              {`Forecast ${formatAmount(point.forecast, amountUnit)}${point.year_status === "PARTIAL_YEAR" ? ` · ${point.months_included} months` : ""}`}
                            </div>
                          ) : null}
                        </ChartTooltip>
                      );
                    }}
                  />
                  <Bar
                    dataKey="forecast"
                    name="Forecast"
                    fill={palette.forecast}
                    radius={SOLID_BAR_RADIUS}
                    maxBarSize={24}
                    isAnimationActive={false}
                  />
                </BarChart>
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
  const palette = useChartPalette();
  const [categories, setCategories] = useState([]);
  const [categoryId, setCategoryId] = useState("");
  const [history, setHistory] = useState(null);
  const [historyStatus, setHistoryStatus] = useState("loading");
  const [historyError, setHistoryError] = useState("");
  const [restoreStatus, setRestoreStatus] = useState(forecast ? "ready" : "loading");
  const [restoreError, setRestoreError] = useState("");
  const [activeTab, setActiveTab] = useState("historical");
  const [historyRange, setHistoryRange] = useState(HISTORY_RANGE_LATEST_2_YEARS);
  const [historyRequestId, setHistoryRequestId] = useState(0);
  const [comparisonHistory, setComparisonHistory] = useState(null);
  const historyEpochRef = useRef(readOverviewHistoryEpoch());

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
          items.find((item) => item.id === catalog.default_category) || items[0];
        if (!selected) {
          setHistoryStatus("empty");
          setHistoryError("No categories were found in the historical data.");
          return;
        }
        setCategoryId(selected.id);
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
  }, []);

  useEffect(() => {
    if (!categoryId) {
      return undefined;
    }
    let cancelled = false;
    async function loadSelectedCategory() {
      setHistoryStatus("loading");
      setHistoryError("");
      setHistory(null);
      try {
        const payload = await getOverviewAnalysis(categoryId, historyRange);
        if (cancelled) {
          return;
        }
        const invalid = validateHistoricalAnalysis(payload);
        if (invalid) {
          throw new Error(invalid);
        }
        setHistory(payload);
        setHistoryStatus("ready");
      } catch (cause) {
        if (cancelled) {
          return;
        }
        const message = cause.message || "Unable to load historical analysis.";
        setHistory(null);
        if (/no historical actuals/i.test(message)) {
          setHistoryStatus("empty");
          setHistoryError(message);
          return;
        }
        setHistoryStatus("error");
        setHistoryError(message);
      }
    }
    loadSelectedCategory();
    return () => {
      cancelled = true;
    };
  }, [categoryId, historyRequestId, historyRange]);

  useEffect(() => {
    function refreshIfHistoryChanged() {
      const epoch = readOverviewHistoryEpoch();
      if (epoch && epoch !== historyEpochRef.current) {
        historyEpochRef.current = epoch;
        setHistoryRequestId((value) => value + 1);
      }
    }
    function onVisible() {
      if (document.visibilityState === "visible") {
        refreshIfHistoryChanged();
      }
    }
    window.addEventListener("focus", refreshIfHistoryChanged);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", refreshIfHistoryChanged);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  useEffect(() => {
    restoreLatest();
  }, [restoreLatest]);

  useEffect(() => {
    if (!forecast?.category || !categories.length) {
      setComparisonHistory(null);
      return undefined;
    }
    const matched = categories.find((item) => forecastMatchesOverviewCategory(forecast.category, item));
    if (!matched) {
      setComparisonHistory(null);
      return undefined;
    }
    if (history && forecastMatchesOverviewCategory(forecast.category, history.category || history)) {
      setComparisonHistory(history);
      return undefined;
    }
    let cancelled = false;
    getOverviewAnalysis(matched.id)
      .then((payload) => {
        if (!cancelled) {
          setComparisonHistory(payload);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setComparisonHistory(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [forecast?.category, categories, history]);

  const amountUnit = history?.amount_unit;
  const unit = axisUnitLabel(amountUnit);
  const historicalDisplay = useMemo(
    () => buildHistoricalDisplayAnalysis(history, { rangeMode: historyRange }),
    [history, historyRange]
  );
  const ranking = useMemo(
    () => codeHistoryRanking(historicalDisplay?.budget_codes || history?.budget_codes),
    [historicalDisplay, history]
  );
  const monthlyTrend = useMemo(
    () => (Array.isArray(historicalDisplay?.monthly_actuals) ? historicalDisplay.monthly_actuals : []).map((row) => ({
      ...row,
      label: shortHistoryLabel(row.month),
      actual: row.actual_amount == null ? null : Number(row.actual_amount),
    })),
    [historicalDisplay]
  );
  const yearlyTrend = useMemo(
    () => (Array.isArray(historicalDisplay?.yearly_actuals) ? historicalDisplay.yearly_actuals : []).map((row) => ({
      ...row,
      label: row.year_status === "PARTIAL_YEAR" ? `${row.year}*` : String(row.year),
      actual: Number(row.actual_amount),
    })),
    [historicalDisplay]
  );
  const seasonalTrend = useMemo(
    () => (Array.isArray(historicalDisplay?.seasonal_profile) ? historicalDisplay.seasonal_profile : []).map((row) => ({
      ...row,
      average: row.average_amount == null ? null : Number(row.average_amount),
    })),
    [historicalDisplay]
  );
  const selectedCategory = categories.find((item) => item.id === categoryId) || history?.category || null;
  const submissionMeta = useMemo(() => latestSubmissionMeta(history), [history]);
  const yearNote = useMemo(
    () => partialYearNote(yearlyTrend, history),
    [yearlyTrend, history]
  );
  // Forecast / Comparison always use the loaded forecast scope — never the Historical category filter.
  const mode = analyticsPageMode({ historyStatus, history, forecast: forecast || null });
  useEffect(() => {
    if (!isAnalyticsTab(activeTab)) {
      setActiveTab(defaultAnalyticsTab(mode));
    }
  }, [activeTab, mode]);
  const comparisonActuals = useMemo(() => {
    if (!forecast) {
      return [];
    }
    // Prefer Overview monthly actuals for the forecast category. Forecast payload
    // historical_actuals omit months where any selected code is incomplete, which
    // must not blank Comparison All history bars when the master series has values.
    const overviewMonths = comparisonHistory
      && forecastMatchesOverviewCategory(forecast.category, comparisonHistory.category || comparisonHistory)
      ? comparisonHistory.monthly_actuals
      : null;
    return mergeHistoricalActuals(overviewMonths, forecast.historical_actuals);
  }, [forecast, comparisonHistory]);
  const comparisonRows = useMemo(() => {
    if (!forecast) {
      return [];
    }
    return buildForecastComparison({
      historicalActuals: comparisonActuals,
      forecast,
      historyEnd: forecast.history_end || forecast.historical_end,
    });
  }, [forecast, comparisonActuals]);
  const budgetTrend = useMemo(() => {
    if (!forecast) {
      return null;
    }
    return buildBudgetTrend({
      monthlyActuals: comparisonActuals,
      forecast,
    });
  }, [forecast, comparisonActuals]);
  const comparisonCoverageNote = useMemo(() => {
    if (!forecast) {
      return "";
    }
    const forecastCodes = Number(forecast.selected_account_count) || 0;
    const sameCategory = comparisonHistory
      ? forecastMatchesOverviewCategory(forecast.category, comparisonHistory.category || comparisonHistory)
      : false;
    const historyCodes = sameCategory ? Number(comparisonHistory?.budget_code_count) || 0 : 0;
    const historyStart = budgetTrend?.history_start
      || comparisonHistory?.history_start
      || forecast.historical_start
      || forecast.historical_actuals?.[0]?.month;
    const historyEnd = budgetTrend?.history_end
      || comparisonHistory?.history_end
      || forecast.history_end
      || forecast.historical_end;
    const parts = [];
    if (historyStart && historyEnd) {
      parts.push(
        `Actuals used for comparison cover ${monthTitle(historyStart)} – ${monthTitle(historyEnd)} for category ${forecast.category || "—"}.`
      );
    }
    if (sameCategory && historyCodes > 0 && forecastCodes > 0 && historyCodes !== forecastCodes) {
      parts.push(
        `Budget-code coverage differs: historical analysis lists ${historyCodes} codes while this forecast includes ${forecastCodes}.`
      );
    } else if (forecastCodes > 0) {
      parts.push(`This forecast includes ${forecastCodes} Budget Codes.`);
    }
    return parts.join(" ");
  }, [forecast, comparisonHistory, budgetTrend]);

  return (
    <section className="analytics-page">
      <header className="page-header">
        <div>
          <h1>Analytics & Charts</h1>
        </div>
      </header>

      <div className="analytics-toolbar">
        <div className="analytics-toolbar-tabs">
          <div className="analytics-tabs" role="tablist" aria-label="Analytics views">
            {ANALYTICS_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={`analytics-tab-${tab.id}`}
                aria-selected={activeTab === tab.id}
                aria-controls={`analytics-panel-${tab.id}`}
                className={activeTab === tab.id ? "active" : ""}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
        {activeTab === "historical" ? (
          <div className="analytics-toolbar-controls">
            <label className="analytics-category-control" htmlFor="analytics-category">
              <span>Category</span>
              <select
                id="analytics-category"
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
                disabled={!categories.length || historyStatus === "loading"}
              >
                {categories.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="analytics-history-view" role="group" aria-label="Historical display range">
              <button
                type="button"
                className={historyRange === HISTORY_RANGE_LATEST_2_YEARS ? "active" : ""}
                onClick={() => setHistoryRange(HISTORY_RANGE_LATEST_2_YEARS)}
              >
                Latest 2 years
              </button>
              <button
                type="button"
                className={historyRange === HISTORY_RANGE_ALL ? "active" : ""}
                onClick={() => setHistoryRange(HISTORY_RANGE_ALL)}
              >
                All history
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <div
        className="analytics-tab-panel"
        role="tabpanel"
        id="analytics-panel-historical"
        aria-labelledby="analytics-tab-historical"
        hidden={activeTab !== "historical"}
      >
      {history ? (
        <section className="analytics-meta-grid" aria-label="Actuals coverage">
          <div className="analytics-meta-item">
            <span>Actual-data coverage</span>
            <strong>{actualDataCoverageLabel(history)}</strong>
          </div>
          <div className="analytics-meta-item">
            <span>Displayed range</span>
            <strong>
              {historicalDisplay?.window?.startIso && historicalDisplay?.window?.endIso
                ? `${monthTitle(historicalDisplay.window.startIso)} – ${monthTitle(historicalDisplay.window.endIso)}`
                : "—"}
            </strong>
          </div>
          <div className="analytics-meta-item">
            <span>Latest complete year</span>
            <strong>{latestCompleteYearDisplay(history)}</strong>
          </div>
          <div className="analytics-meta-item">
            <span>Latest submission</span>
            <strong>
              <span className="analytics-submission-line">
                <span>{submissionMeta.monthLabel}</span>
                {submissionMeta.status === "Partial" ? (
                  <span className="analytics-partial-badge">Partial</span>
                ) : null}
                {submissionMeta.submitted != null && submissionMeta.applicable != null ? (
                  <span>{`${submissionMeta.submitted}/${submissionMeta.applicable} codes submitted`}</span>
                ) : null}
              </span>
            </strong>
            {submissionMeta.scopeLabel ? (
              <span className="analytics-meta-scope">{submissionMeta.scopeLabel}</span>
            ) : null}
          </div>
        </section>
      ) : null}

      {yearNote ? <p className="analytics-partial-note">{yearNote}</p> : null}

      <details className="analytics-methods">
        <summary>How coverage and YoY are calculated</summary>
        <div>
          Actual-data coverage is the full stored monthly history for the selected category.
          Displayed range follows Latest 2 years (two complete calendar years in confirmed data) or All history.
          Switching this range does not change a generated forecast. Year-on-year growth compares only
          overlapping months in partial years within the displayed range.
        </div>
      </details>

      <section className="analytics-major-section" aria-labelledby="analytics-historical-heading">
        <AnalyticsSectionHeading
          id="analytics-historical-heading"
          title={selectedCategory?.name ? `Historical Budget Analysis · ${selectedCategory.name}` : "Historical Budget Analysis"}
          subtitle={historicalDisplay?.displayedRangeLabel || historicalDisplay?.periodLabel || null}
        />

      {mode.showHistoricalError ? (
        <div className="card overview-inline-error" role="alert">
          <p>{historyError}</p>
          <button
            className="generate-button"
            type="button"
            onClick={() => setHistoryRequestId((value) => value + 1)}
          >
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

      {mode.showHistorical && historicalDisplay?.emptyReason ? (
        <article className="card empty-panel">
          <EmptyState message={historicalDisplay.emptyReason} />
        </article>
      ) : null}

      {mode.showHistorical && !historicalDisplay?.emptyReason ? (
        <>

          <ul className="analytics-kpis" aria-label="Historical snapshot">
            <li>
              <span>Total actuals</span>
              <strong>{formatAmount(historicalDisplay?.overall_total ?? history.overall_total, amountUnit)}</strong>
            </li>
            <li>
              <span>Monthly average</span>
              <strong>{formatAmount(historicalDisplay?.monthly_average ?? history.monthly_average, amountUnit)}</strong>
              <em className="analytics-kpi-note">
                {historyRange === HISTORY_RANGE_ALL
                  ? "Average over All history months shown"
                  : "Average over the Latest 2 years shown"}
              </em>
            </li>
            <li>
              <span>Latest YoY growth</span>
              <strong className={growthClass(historicalDisplay?.latest_year_growth_percent ?? history.latest_year_growth_percent)}>
                {formatPercentChange(historicalDisplay?.latest_year_growth_percent ?? history.latest_year_growth_percent)}
              </strong>
            </li>
            <li>
              <span>Recurring codes</span>
              <strong>{`${historicalDisplay?.recurring_count ?? history.recurring_count ?? 0} / ${historicalDisplay?.budget_code_count ?? history.budget_code_count ?? ranking.length}`}</strong>
            </li>
          </ul>

          <div className="analytics-history-grid">
            <article className="card analytics-main analytics-span">
              <div className="analytics-main-head">
                <div>
                  <h2>Expenditure trend</h2>
                  <p className="muted">Combined monthly actuals for the selected historical period.</p>
                </div>
              </div>
              <HistoricalMonthlyTrendChart monthlyTrend={monthlyTrend} amountUnit={amountUnit} unit={unit} />
            </article>

            <article className="card analytics-main">
              <div className="analytics-main-head">
                <div>
                  <h2>Seasonal variation</h2>
                  <p className="muted">Average actuals by calendar month within the selected period.</p>
                </div>
              </div>
              <div className="analytics-chart" role="img" aria-label="Seasonal variation">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={seasonalTrend} margin={cartesianChartMargin()} barCategoryGap="18%">
                    <CartesianGrid stroke={palette.grid} vertical={false} />
                    <XAxis dataKey="month_label" tick={{ fontSize: 11, fill: palette.tick }} tickMargin={6} />
                    <YAxis {...valueYAxisProps(unit, { axisFill: palette.axis })} tickFormatter={formatAxisTick} />
                    <Tooltip
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none", maxWidth: "min(320px, calc(100vw - 24px))" }}
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
                    <Bar dataKey="average" name="Seasonal pattern" fill={palette.seasonal || palette.purple} radius={SOLID_BAR_RADIUS} maxBarSize={24} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </article>

            <article className="card analytics-main">
              <div className="analytics-main-head">
                <div>
                  <h2>Yearly growth</h2>
                  <p className="muted">Annual totals in the selected period, with matching-month YoY growth.</p>
                </div>
              </div>
              <div className="analytics-chart" role="img" aria-label="Yearly growth">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={yearlyTrend} margin={cartesianChartMargin()} barCategoryGap="18%">
                    <CartesianGrid stroke={palette.grid} vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: palette.tick }} tickMargin={6} />
                    <YAxis {...valueYAxisProps(unit, { axisFill: palette.axis })} tickFormatter={formatAxisTick} />
                    <Tooltip
                      wrapperStyle={{ zIndex: 5, pointerEvents: "none", maxWidth: "min(320px, calc(100vw - 24px))" }}
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
                    <Bar dataKey="actual" name="Annual actual" fill={palette.historical} radius={SOLID_BAR_RADIUS} maxBarSize={28} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </article>

            <article className="card analytics-rank analytics-span">
              <h2>Recurring expenses</h2>
              <p className="muted">
                Budget Codes ranked by actual total in the selected period. Recurring codes appear in at least 75% of months in that period.
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
      </div>

      <div
        className="analytics-tab-panel"
        role="tabpanel"
        id="analytics-panel-forecast"
        aria-labelledby="analytics-tab-forecast"
        hidden={activeTab !== "forecast"}
      >
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

      {restoreStatus === "loading" && !forecast ? (
        <article className="card">
          <LoadingState label="Checking saved forecast..." />
        </article>
      ) : null}

      {mode.showForecastAnalysis ? (
        <ForecastAnalysisPanel forecast={forecast} />
      ) : restoreStatus !== "loading" ? (
        <section className="analytics-major-section" aria-labelledby="analytics-forecast-heading">
          <AnalyticsSectionHeading id="analytics-forecast-heading" title="Forecast Analysis" />
          <article className="card empty-panel analytics-forecast-optional">
            <EmptyState message="Forecast Analysis is available after a forecast is generated." />
            <Link className="generate-button overview-link" to="/generate-forecast">
              Go to Generate Forecast
            </Link>
          </article>
        </section>
      ) : null}
      </div>

      <div
        className="analytics-tab-panel"
        role="tabpanel"
        id="analytics-panel-comparison"
        aria-labelledby="analytics-tab-comparison"
        hidden={activeTab !== "comparison"}
      >
      {mode.showComparison ? (
        <>
          <BudgetTrendAnalysis
            trend={budgetTrend}
            amountUnit={forecast?.amount_unit}
          />
          <ForecastComparison
            rows={comparisonRows}
            amountUnit={forecast?.amount_unit}
            forecast={forecast}
            historyStart={budgetTrend?.history_start || forecast?.historical_start}
            historyEnd={budgetTrend?.history_end || forecast?.history_end || forecast?.historical_end}
            coverageNote={comparisonCoverageNote}
          />
        </>
      ) : (
        <section className="analytics-major-section" aria-labelledby="analytics-comparison-heading">
          <AnalyticsSectionHeading id="analytics-comparison-heading" title="Historical vs Forecast Comparison" />
          <article className="card empty-panel analytics-forecast-optional">
            <EmptyState message="Comparison is available after a forecast is generated." />
            <Link className="generate-button overview-link" to="/generate-forecast">
              Go to Generate Forecast
            </Link>
          </article>
        </section>
      )}
      </div>
    </section>
  );
}
