import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { getForecastHistory, getForecastRecord } from "../api/forecastApi";
import ErrorState from "../components/ErrorState";
import ForecastTable from "../components/ForecastTable";
import LoadingState from "../components/LoadingState";
import { useForecast } from "../context/ForecastContext";
import { chronologicalForecasts, formatAmount } from "../utils/forecastDisplay";
import { isoToLabel } from "../utils/months";

function recordPeriod(record) {
  if (!record?.requested_start_month || !record?.requested_end_month) {
    return "—";
  }
  return `${isoToLabel(record.requested_start_month)} to ${isoToLabel(record.requested_end_month)}`;
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

export default function ForecastHistory() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { applyForecast } = useForecast();
  const selectedId = Number(searchParams.get("id") || 0);
  const [records, setRecords] = useState([]);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");

  const loadRecords = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const payload = await getForecastHistory(50);
      setRecords(Array.isArray(payload.records) ? payload.records : []);
    } catch (cause) {
      setRecords([]);
      setError(cause.message || "Unable to load Forecast History.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRecords();
  }, [loadRecords]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError("");
      return undefined;
    }
    let cancelled = false;
    async function loadDetail() {
      setDetailLoading(true);
      setDetailError("");
      try {
        const payload = await getForecastRecord(selectedId);
        if (!cancelled) {
          setDetail(payload);
        }
      } catch (cause) {
        if (!cancelled) {
          setDetail(null);
          setDetailError(cause.message || "Unable to load the forecast record.");
        }
      } finally {
        if (!cancelled) {
          setDetailLoading(false);
        }
      }
    }
    loadDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  function selectRecord(runId) {
    setSearchParams({ id: String(runId) });
  }

  function openInAnalytics() {
    if (!detail) {
      return;
    }
    applyForecast(detail);
    navigate("/analytics");
  }

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>Forecast History</h1>
          <p>System-wide forecast records saved after each successful Generate Forecast run.</p>
        </div>
      </header>

      {error ? <ErrorState message={error} /> : null}

      {loading ? (
        <article className="card">
          <LoadingState label="Loading Forecast History..." />
        </article>
      ) : !records.length && !error ? (
        <article className="card empty-panel">
          <p>No system-wide forecast records yet.</p>
          <Link className="generate-button overview-link" to="/generate-forecast">
            Go to Generate Forecast
          </Link>
        </article>
      ) : records.length ? (
        <article className="card table-card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Generated at</th>
                  <th>Category</th>
                  <th>Forecast period</th>
                  <th>Months</th>
                  <th>Overall total</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr
                    key={record.id}
                    className={`history-row${record.id === selectedId ? " active" : ""}`}
                    onClick={() => selectRecord(record.id)}
                  >
                    <td>{formatGeneratedAt(record.generated_at)}</td>
                    <td>{record.category}</td>
                    <td>{recordPeriod(record)}</td>
                    <td>{record.forecast_month_count}</td>
                    <td>{formatAmount(record.overall_total, record.amount_unit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}

      {selectedId ? (
        <article className="card table-card history-detail">
          <div className="history-detail-head">
            <h2>Selected forecast record</h2>
            <button className="generate-button" type="button" onClick={openInAnalytics} disabled={!detail || detailLoading}>
              Open in Analytics
            </button>
          </div>
          {detailError ? <ErrorState message={detailError} /> : null}
          {detailLoading ? <LoadingState label="Loading monthly results..." /> : null}
          {detail ? (
            <>
              <dl className="summary-list history-summary">
                <div>
                  <dt>Forecast period</dt>
                  <dd>{recordPeriod(detail)}</dd>
                </div>
                <div>
                  <dt>Overall total</dt>
                  <dd>{formatAmount(detail.overall_total, detail.amount_unit)}</dd>
                </div>
                <div>
                  <dt>Monthly average</dt>
                  <dd>{formatAmount(detail.monthly_average, detail.amount_unit)}</dd>
                </div>
                <div>
                  <dt>Generated at</dt>
                  <dd>{formatGeneratedAt(detail.generated_at)}</dd>
                </div>
              </dl>
              <ForecastTable
                monthlyForecasts={chronologicalForecasts(detail)}
                overallTotal={detail.overall_total}
                amountUnit={detail.amount_unit}
              />
            </>
          ) : null}
        </article>
      ) : records.length ? (
        <p className="muted history-hint">Select a record to inspect monthly results.</p>
      ) : null}
    </section>
  );
}
