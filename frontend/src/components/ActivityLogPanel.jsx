import { useCallback, useEffect, useRef, useState } from "react";
import { listActivityEvents } from "../api/datasetApi";

const PAGE_SIZE = 20;
const REFRESH_MS = 15000;

function colomboToday() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Colombo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function formatStamp(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString("en-GB", {
    timeZone: "Asia/Colombo",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDetails(details) {
  if (!details || (typeof details === "object" && !Array.isArray(details) && Object.keys(details).length === 0)) {
    return "—";
  }
  if (typeof details === "string") {
    return details;
  }
  const lines = [];
  if (details.before != null || details.after != null) {
    lines.push(`Before: ${stringifyValue(details.before)}`);
    lines.push(`After: ${stringifyValue(details.after)}`);
  }
  if (Array.isArray(details.edits) && details.edits.length) {
    const sample = details.edits.slice(0, 8).map((item) => {
      const code = item.budget_code || "?";
      const month = item.month || "";
      return `${code}${month ? ` ${month}` : ""}: ${stringifyValue(item.before)} → ${stringifyValue(item.after)}`;
    });
    lines.push(...sample);
    if (details.edits.length > 8) {
      lines.push(`…and ${details.edits.length - 8} more edits`);
    }
  }
  for (const key of ["filename", "sheet_name", "message", "reason", "error", "inserted", "changed", "edit_count", "bytes", "state", "code"]) {
    if (details[key] != null && details[key] !== "") {
      lines.push(`${labelize(key)}: ${stringifyValue(details[key])}`);
    }
  }
  if (!lines.length) {
    try {
      return JSON.stringify(details);
    } catch {
      return "—";
    }
  }
  return lines.join("\n");
}

function labelize(key) {
  return String(key).replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function stringifyValue(value) {
  if (value == null || value === "") {
    return "—";
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function statusClass(status) {
  const text = String(status || "").toLowerCase();
  if (text === "succeeded" || text === "success") {
    return "is-success";
  }
  if (text === "failed" || text === "failure" || text === "error") {
    return "is-failed";
  }
  return "";
}

export default function ActivityLogPanel({ active }) {
  const [day, setDay] = useState(() => colomboToday());
  const [page, setPage] = useState(1);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const today = colomboToday();
  const isToday = day === today;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const load = useCallback(async (nextPage = page, nextDay = day, options = {}) => {
    const id = ++requestId.current;
    if (!options.silent) {
      setLoading(true);
    }
    try {
      const payload = await listActivityEvents({ page: nextPage, pageSize: PAGE_SIZE, day: nextDay });
      if (id !== requestId.current) {
        return;
      }
      setItems(payload?.items || []);
      setTotal(Number(payload?.total) || 0);
      setError("");
      if (payload?.day && payload.day !== nextDay) {
        setDay(payload.day);
      }
    } catch (cause) {
      if (id !== requestId.current) {
        return;
      }
      setError(cause.message || "Activity log could not be loaded.");
      if (!options.silent) {
        setItems([]);
        setTotal(0);
      }
    } finally {
      if (id === requestId.current && !options.silent) {
        setLoading(false);
      }
    }
  }, [day, page]);

  useEffect(() => {
    if (!active) {
      return undefined;
    }
    load(page, day);
    return undefined;
  }, [active, page, day, load]);

  useEffect(() => {
    if (!active || !isToday) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      load(page, day, { silent: true });
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [active, isToday, page, day, load]);

  return (
    <article className="card section-card activity-log-card">
      <div className="activity-log-header">
        <div>
          <h2>Activity Log</h2>
          <p className="muted">Settings actions recorded after this feature was enabled. Times use Asia/Colombo.</p>
        </div>
        <label className="activity-log-date" htmlFor="activity-log-day">
          <span>Date</span>
          <input
            id="activity-log-day"
            type="date"
            value={day}
            max={today}
            onChange={(event) => {
              const next = event.target.value || colomboToday();
              setPage(1);
              setDay(next);
            }}
          />
        </label>
      </div>

      {error ? (
        <div className="settings-ops-alert" role="alert">
          <p>{error}</p>
          <button type="button" className="export-button-secondary" onClick={() => load(page, day)}>
            Retry
          </button>
        </div>
      ) : null}

      {loading && !items.length ? <p className="settings-loading">Loading activity…</p> : null}

      {!loading && !error && items.length === 0 ? (
        <p className="muted activity-log-empty">No activities recorded for this date.</p>
      ) : null}

      {items.length > 0 ? (
        <div className="table-wrap activity-log-table-wrap">
          <table className="activity-log-table">
            <thead>
              <tr>
                <th>Date &amp; Time</th>
                <th>User</th>
                <th>Activity</th>
                <th>Target</th>
                <th>Status</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{formatStamp(item.occurred_at)}</td>
                  <td>{item.user || "—"}</td>
                  <td>{item.activity || item.action || "—"}</td>
                  <td className="activity-log-target">{item.target || "—"}</td>
                  <td>
                    <span className={`activity-log-status ${statusClass(item.status)}`}>
                      {item.status || "—"}
                    </span>
                  </td>
                  <td>
                    <pre className="activity-log-details">{formatDetails(item.details)}</pre>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {!error && total > PAGE_SIZE ? (
        <div className="settings-pagination">
          <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}>
            Previous
          </button>
          <span>
            Page {page} of {pageCount}
          </span>
          <button
            type="button"
            disabled={page >= pageCount || loading}
            onClick={() => setPage((value) => value + 1)}
          >
            Next
          </button>
        </div>
      ) : null}
    </article>
  );
}
