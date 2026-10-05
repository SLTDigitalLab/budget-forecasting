import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getForecastHistory,
  getForecastRecord,
  getOverviewCategories,
  getOverviewHistorical,
} from "../api/forecastApi";
import SummaryCard from "../components/SummaryCard";
import { cartesianChartMargin, valueYAxisProps } from "../utils/chartLayout";
import { axisUnitLabel, formatAmount, formatAxisTick } from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";
import {
  actualYearSeries,
  forecastViewFromRun,
  latestCompleteActualYear,
  selectNewestForecast,
  shareDistribution,
  sortForecastHistoryNewestFirst,
  STORED_FORECAST_STATUS,
} from "../utils/overviewDashboard";
import { compactOverviewError, overviewHistoricalNote } from "../utils/overviewHistorical";

const CODE_COLORS = ["#1f7ae0", "#18b36a", "#1ec8e0", "#f59e0b", "#7c3aed", "#e11d48", "#0ea5e9", "#65a30d", "#db2777", "#0f766e"];

function monthTitle(isoMonth) {
  return isoToLabel(isoMonth || "").replace("-", " ");
}

function recordPeriod(record) {
  if (!record?.requested_start_month || !record?.requested_end_month) {
    return "—";
  }
  return `${isoToLabel(record.requested_start_month)} – ${isoToLabel(record.requested_end_month)}`;
}

function formatGeneratedAt(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatSharePercent(percent) {
  if (percent == null || percent === "" || !Number.isFinite(Number(percent))) {
    return "—";
  }
  return `${(Number(percent) * 100).toFixed(1)}%`;
}

function codeColor(code) {
  const text = String(code || "");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
  }
  return CODE_COLORS[hash % CODE_COLORS.length];
}

function ChartTooltip({ children }) {
  return <div className="chart-tooltip">{children}</div>;
}

function EmptyCopy({ children }) {
  return <p className="muted overview-empty">{children}</p>;
}

function DistributionCard({ title, rows, amountUnit, emptyMessage }) {
  const slices = rows.map((row) => ({ ...row, color: codeColor(row.budget_code) }));
  return (
    <article className="card section-card">
      <h2 className="section-card-header">{title}</h2>
      <div className="section-card-body">
        {slices.length ? (
          <div className="overview-distribution">
            <div className="overview-forecast-donut">
              <div className="overview-forecast-donut-chart">
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie
                      data={slices}
                      dataKey="amount"
                      nameKey="budget_code"
                      innerRadius={52}
                      outerRadius={72}
                      paddingAngle={0}
                      startAngle={90}
                      endAngle={-270}
                      isAnimationActive={false}
                    >
                      {slices.map((row) => (
                        <Cell key={row.budget_code} fill={row.color} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                <div className="overview-forecast-donut-center">
                  <strong>{slices.length}</strong>
                  <span>Budget Codes</span>
                </div>
              </div>
            </div>
            <div
              className="overview-forecast-code-list-scroll data-scroll data-scroll-sm"
              tabIndex={0}
              aria-label={`Scrollable Budget Code distribution, ${slices.length} Budget Codes`}
            >
              <table className="overview-mini-table">
                <thead>
                  <tr>
                    <th>Budget Code</th>
                    <th>Amount (LKR Mn)</th>
                    <th>Share</th>
                  </tr>
                </thead>
                <tbody>
                  {slices.map((row) => (
                    <tr key={row.budget_code}>
                      <td>
                        <span className="legend-swatch" style={{ background: row.color }} />
                        {row.budget_code}
                      </td>
                      <td>{formatAmount(row.amount, amountUnit)}</td>
                      <td>{formatSharePercent(row.percent)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <EmptyCopy>{emptyMessage}</EmptyCopy>
        )}
      </div>
    </article>
  );
}

export default function Overview() {
  const [categories, setCategories] = useState([]);
  const [categoryId, setCategoryId] = useState("");
  const [historyRecords, setHistoryRecords] = useState([]);
  const [historical, setHistorical] = useState(null);
  const [forecastRun, setForecastRun] = useState(null);
  const [forecastView, setForecastView] = useState(null);
  const [loading, setLoading] = useState(true);
  const [historicalLoading, setHistoricalLoading] = useState(false);
  const [forecastLoading, setForecastLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historicalError, setHistoricalError] = useState("");
  const [forecastError, setForecastError] = useState("");
  const [historyReady, setHistoryReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadCatalog() {
      setLoading(true);
      try {
        const [catalog, history] = await Promise.all([
          getOverviewCategories(),
          getForecastHistory(50),
        ]);
        if (cancelled) {
          return;
        }
        const items = Array.isArray(catalog?.categories) ? catalog.categories : [];
        setCategories(items);
        setHistoryRecords(sortForecastHistoryNewestFirst(history?.records));
        setHistoryReady(true);
        const initial = items.find((item) => item.id === catalog?.default_category) || items[0];
        setCategoryId(initial?.id || "");
        if (!items.length) {
          setHistoricalError("No historical actual data is available for this category.");
        }
      } catch (cause) {
        if (!cancelled) {
          setHistoricalError(compactOverviewError(cause.message || "Unable to load Overview categories."));
        }
      } finally {
        if (!cancelled) {
          setHistoryReady(true);
          setLoading(false);
        }
      }
    }
    loadCatalog();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!categoryId) {
      return undefined;
    }
    const category = categories.find((item) => item.id === categoryId);
    if (!category) {
      return undefined;
    }
    let cancelled = false;
    async function loadCategory() {
      setHistoricalLoading(true);
      setHistorical(null);
      setHistoricalError("");
      try {
        const payload = await getOverviewHistorical(category.id);
        if (!cancelled) {
          setHistorical(payload);
        }
      } catch (cause) {
        if (!cancelled) {
          setHistorical(null);
          setHistoricalError(compactOverviewError(cause.message || "No historical actual data is available for this category."));
        }
      } finally {
        if (!cancelled) {
          setHistoricalLoading(false);
        }
      }
    }
    loadCategory();
    return () => {
      cancelled = true;
    };
  }, [categoryId, categories]);

  useEffect(() => {
    if (!historyReady) {
      return undefined;
    }
    const newest = selectNewestForecast(historyRecords);
    if (!newest?.id) {
      setForecastRun(null);
      setForecastView(null);
      setForecastError("No forecast is available yet.");
      setForecastLoading(false);
      return undefined;
    }
    let cancelled = false;
    async function loadLatestForecast() {
      setForecastLoading(true);
      setForecastError("");
      try {
        const detail = await getForecastRecord(newest.id);
        const view = forecastViewFromRun({
          ...detail,
          id: newest.id,
          category: detail?.category || newest.category,
          generated_at: detail?.generated_at || newest.generated_at,
        });
        if (!cancelled) {
          setForecastRun({ ...newest, ...detail, id: newest.id });
          setForecastView(view);
          if (!view) {
            setForecastError("No forecast is available yet.");
          }
        }
      } catch (cause) {
        if (!cancelled) {
          setForecastRun(null);
          setForecastView(null);
          setForecastError(compactOverviewError(cause.message || "No forecast is available yet."));
        }
      } finally {
        if (!cancelled) {
          setForecastLoading(false);
        }
      }
    }
    loadLatestForecast();
    return () => {
      cancelled = true;
    };
  }, [historyRecords, historyReady]);

  const category = categories.find((item) => item.id === categoryId) || null;
  const categoryName = category?.name || "Category";
  const forecastCategoryName = forecastRun?.category || forecastView?.category || "Category";
  const historicalAmountUnit = historical?.amount_unit;
  const forecastAmountUnit = forecastRun?.amount_unit;
  const historicalAxis = axisUnitLabel(historicalAmountUnit);
  const forecastAxis = axisUnitLabel(forecastAmountUnit);
  const historicalUnit = historicalAxis === "LKR Mn" ? "Amount (LKR Mn)" : historicalAxis;
  const forecastUnit = forecastAxis === "LKR Mn" ? "Amount (LKR Mn)" : forecastAxis;
  const actualYear = latestCompleteActualYear(historical?.latest_year_monthly_actuals);
  const actualPoints = actualYearSeries(historical?.latest_year_monthly_actuals, actualYear);
  const historicalYearMatches = actualYear != null && Number(historical?.latest_complete_year) === Number(actualYear);
  const historicalCodes = historicalYearMatches
    ? shareDistribution(historical?.latest_complete_year_budget_codes, "actual_amount")
    : [];
  const forecastPoints = forecastView?.months || [];
  const forecastYear = forecastView?.forecastYear;
  const historicalNote = overviewHistoricalNote(historical);
  const activities = historyRecords.slice(0, 5);

  return (
    <section className="overview-page">
      <header className="page-header">
        <div>
          <h1>Overview</h1>
          <p>Key insights from your budget forecasting data</p>
        </div>
        <div className="overview-period">
          <span>Forecast Period</span>
          <strong>{forecastRun ? recordPeriod(forecastRun) : "—"}</strong>
        </div>
      </header>

      <div className="field overview-category-field">
        <label htmlFor="overview-category">Select Category</label>
        <select
          id="overview-category"
          value={categoryId}
          onChange={(event) => setCategoryId(event.target.value)}
          disabled={loading || !categories.length}
        >
          {categories.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
      </div>

      {historicalNote ? <p className="overview-historical-note">{historicalNote}</p> : null}

      <div className="summary-grid summary-grid-four overview-kpi-row">
        <SummaryCard label="Category Forecast" value={forecastView ? formatAmount(forecastView.total, forecastAmountUnit) : "—"} />
        <SummaryCard label="Monthly Average" value={forecastView ? formatAmount(forecastView.average, forecastAmountUnit) : "—"} />
        <SummaryCard label="Budget Codes" value={forecastView ? String(forecastView.codeCount) : "—"} />
        <SummaryCard label="Category Share" value={formatSharePercent(forecastView?.share)} />
      </div>

      <div className="overview-split">
        <article className="card section-card">
          <h2 className="section-card-header">
            {actualYear ? `Monthly Actuals (Latest Year: ${actualYear}) – ${categoryName}` : `Monthly Actuals – ${categoryName}`}
          </h2>
          <div className="section-card-body overview-chart-body">
            {historicalLoading && !historical ? (
              <EmptyCopy>Loading historical actuals…</EmptyCopy>
            ) : historicalError ? (
              <EmptyCopy>{historicalError}</EmptyCopy>
            ) : !historical?.latest_year_monthly_actuals?.length ? (
              <EmptyCopy>No historical actual data is available for this category.</EmptyCopy>
            ) : actualYear == null ? (
              <EmptyCopy>No complete 12-month actual year is available for this category.</EmptyCopy>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={actualPoints} margin={cartesianChartMargin()}>
                  <CartesianGrid stroke="#e6eef6" vertical={false} />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={6} />
                  <YAxis {...valueYAxisProps(historicalUnit)} tickLine={false} axisLine={false} tickFormatter={formatAxisTick} />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) {
                        return null;
                      }
                      const point = payload[0].payload;
                      return (
                        <ChartTooltip>
                          <div className="chart-tooltip-month">{monthTitle(point.month)}</div>
                          <div>{`Category: ${categoryName}`}</div>
                          <div>{`Actual amount: ${point.actual_amount == null ? "—" : formatAmount(point.actual_amount, historicalAmountUnit)}`}</div>
                          <div>{`Year: ${point.year}`}</div>
                        </ChartTooltip>
                      );
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="actual_amount"
                    name="Actual"
                    stroke="#1f7ae0"
                    strokeWidth={2.4}
                    dot={{ r: 3 }}
                    connectNulls={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </article>
        <DistributionCard
          title={`Budget Code Distribution – ${categoryName}`}
          rows={historicalCodes}
          amountUnit={historicalAmountUnit}
          emptyMessage={
            historicalLoading
              ? "Loading historical actuals…"
              : historicalError
                ? historicalError
                : !historical?.latest_year_monthly_actuals?.length
                  ? "No historical actual data is available for this category."
                  : actualYear == null
                    ? "No complete 12-month actual year is available for this category."
                    : "Budget Code breakdown is not available for this record."
          }
        />
      </div>

      <section className="overview-latest-forecast" aria-label="Latest Forecast">
        <h2>Latest Forecast</h2>
        <p>{forecastCategoryName !== "Category" ? forecastCategoryName : "—"}</p>
        <p>{forecastRun ? recordPeriod(forecastRun) : "—"}</p>
        <p>{forecastRun?.generated_at ? `Generated ${formatGeneratedAt(forecastRun.generated_at)}` : ""}</p>
      </section>

      <div className="overview-split">
        <article className="card section-card">
          <h2 className="section-card-header">
            {forecastYear ? `Monthly Forecast (${forecastYear}) – ${forecastCategoryName}` : `Monthly Forecast – ${forecastCategoryName}`}
          </h2>
          <div className="section-card-body overview-chart-body">
            {forecastLoading && !forecastView ? (
              <EmptyCopy>Loading latest forecast…</EmptyCopy>
            ) : forecastError || !forecastView ? (
              <EmptyCopy>{forecastError || "No forecast is available yet."}</EmptyCopy>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={forecastPoints} margin={cartesianChartMargin()}>
                  <CartesianGrid stroke="#e6eef6" vertical={false} />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={6} />
                  <YAxis {...valueYAxisProps(forecastUnit)} tickLine={false} axisLine={false} tickFormatter={formatAxisTick} />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) {
                        return null;
                      }
                      const point = payload[0].payload;
                      return (
                        <ChartTooltip>
                          <div className="chart-tooltip-month">{monthTitle(point.month)}</div>
                          <div>{`Category: ${forecastCategoryName}`}</div>
                          <div>{`Forecast amount: ${formatAmount(point.forecast_amount, forecastAmountUnit)}`}</div>
                          <div>{`Forecast period: ${recordPeriod(forecastRun)}`}</div>
                        </ChartTooltip>
                      );
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="forecast_amount"
                    name="Forecast"
                    stroke="#023E8A"
                    strokeWidth={2.4}
                    dot={{ r: 3 }}
                    connectNulls={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </article>
        <DistributionCard
          title={`Budget Code Distribution (Latest Forecast) – ${forecastCategoryName}`}
          rows={forecastView?.codes || []}
          amountUnit={forecastAmountUnit}
          emptyMessage={
            forecastLoading && !forecastView
              ? "Loading latest forecast…"
              : forecastView
                ? "Budget Code breakdown is not available for this record."
                : (forecastError || "No forecast is available yet.")
          }
        />
      </div>

      <article className="card section-card overview-activity-card">
        <div className="section-card-header overview-card-head">
          <h2>Recent Forecasting Activities</h2>
          <Link className="overview-card-link" to="/forecast-history">View All History →</Link>
        </div>
        <div className="section-card-body">
          {historyError ? (
            <EmptyCopy>{historyError}</EmptyCopy>
          ) : activities.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Date & Time</th>
                    <th>Forecast Period</th>
                    <th>Category</th>
                    <th>No. of Codes</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {activities.map((record) => (
                    <tr key={record.id}>
                      <td>{formatGeneratedAt(record.generated_at)}</td>
                      <td>{recordPeriod(record)}</td>
                      <td>{record.category || "—"}</td>
                      <td>{record.selected_account_count}</td>
                      <td>{STORED_FORECAST_STATUS}</td>
                      <td><Link to={`/forecast-history?id=${record.id}`}>View</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyCopy>No recent forecasting activities. Successful Generate Forecast runs appear here.</EmptyCopy>
          )}
        </div>
      </article>
    </section>
  );
}
