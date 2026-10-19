import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  downloadForecastExcel,
  downloadForecastPdf,
  forecastPdfPreviewUrl,
  getForecastHistory,
  getForecastRecord,
} from "../api/forecastApi";
import ErrorState from "../components/ErrorState";
import EmptyState from "../components/EmptyState";
import ForecastReportPreview from "../components/ForecastReportPreview";
import LoadingState from "../components/LoadingState";
import { formatAmount } from "../utils/forecastDisplay";
import {
  buildReportPreviewModel,
  exportPageState,
  excelFileStem,
  reportFileStem,
} from "../utils/forecastReport";
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

function DownloadBar({ disabled, onPdf, onExcel, onOpenPdf, downloadError }) {
  return (
    <div className="export-download-bar">
      <button className="generate-button" type="button" onClick={onPdf} disabled={disabled}>
        Download PDF
      </button>
      <button className="generate-button export-button-secondary" type="button" onClick={onExcel} disabled={disabled}>
        Download Excel
      </button>
      <button className="generate-button export-button-secondary" type="button" onClick={onOpenPdf} disabled={disabled}>
        Open PDF Preview
      </button>
      {downloadError ? <ErrorState message={downloadError} /> : null}
    </div>
  );
}

export default function ExportForecasts() {
  const [records, setRecords] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [previewStatus, setPreviewStatus] = useState("idle");
  const [preview, setPreview] = useState(null);
  const [listError, setListError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const pageState = exportPageState({ selectedId, previewStatus });

  const loadRecords = useCallback(async () => {
    setLoadingList(true);
    setListError("");
    try {
      const payload = await getForecastHistory(50);
      setRecords(Array.isArray(payload.records) ? payload.records : []);
    } catch (cause) {
      setRecords([]);
      setListError(cause.message || "Unable to load completed forecasts.");
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    loadRecords();
  }, [loadRecords]);

  function handleSelect(event) {
    setSelectedId(event.target.value);
    setPreview(null);
    setPreviewStatus("idle");
    setPreviewError("");
    setDownloadError("");
  }

  async function handlePreview() {
    if (!selectedId) {
      return;
    }
    setPreviewStatus("loading");
    setPreviewError("");
    setDownloadError("");
    setPreview(null);
    try {
      const forecast = await getForecastRecord(selectedId);
      const model = buildReportPreviewModel(forecast);
      if (!model) {
        throw new Error("Only a completed forecast can be previewed.");
      }
      setPreview(model);
      setPreviewStatus("ready");
    } catch (cause) {
      setPreview(null);
      setPreviewStatus("error");
      setPreviewError(cause.message || "Unable to load the report preview.");
    }
  }

  async function handleDownload(kind) {
    if (!selectedId || !preview) {
      return;
    }
    setDownloading(kind);
    setDownloadError("");
    const filename = `${
      kind === "excel"
        ? preview.excel_filename_stem || excelFileStem(preview)
        : preview.filename_stem || reportFileStem(preview)
    }.${kind === "pdf" ? "pdf" : "xlsx"}`;
    try {
      if (kind === "pdf") {
        await downloadForecastPdf(selectedId, filename);
      } else {
        await downloadForecastExcel(selectedId, filename);
      }
    } catch (cause) {
      setDownloadError(cause.message || "File generation failed.");
    } finally {
      setDownloading("");
    }
  }

  function handleOpenPdf() {
    if (!selectedId) {
      return;
    }
    window.open(forecastPdfPreviewUrl(selectedId), "_blank", "noopener,noreferrer");
  }

  const selectedRecord = records.find((record) => String(record.id) === String(selectedId));

  return (
    <section className="export-page">
      <header className="page-header">
        <div>
          <h1>Export Forecasts</h1>
          <p>Preview a completed forecast report, then download PDF or Excel from the stored run.</p>
        </div>
      </header>

      {listError ? <ErrorState message={listError} /> : null}

      <article className="card export-selector">
        <h2>Select a completed forecast</h2>
        {loadingList ? (
          <LoadingState label="Loading completed forecasts..." />
        ) : records.length ? (
          <>
            <div className="field">
              <label htmlFor="export-forecast-run">Completed forecast run</label>
              <select id="export-forecast-run" value={selectedId} onChange={handleSelect}>
                <option value="">Select a completed forecast</option>
                {records.map((record) => (
                  <option key={record.id} value={record.id}>
                    {`${formatGeneratedAt(record.generated_at)} · ${recordPeriod(record)} · ${formatAmount(record.overall_total, record.amount_unit)}`}
                  </option>
                ))}
              </select>
            </div>
            {selectedRecord ? (
              <p className="muted">
                {`${selectedRecord.category} · ${recordPeriod(selectedRecord)} · ${selectedRecord.forecast_month_count} months`}
              </p>
            ) : null}
            <button
              className="generate-button"
              type="button"
              onClick={handlePreview}
              disabled={!pageState.showPreviewButton || previewStatus === "loading"}
            >
              Preview Report
            </button>
          </>
        ) : (
          <div className="empty-panel">
            <p>No completed forecast records are available to export.</p>
            <Link className="generate-button overview-link" to="/generate-forecast">
              Go to Generate Forecast
            </Link>
          </div>
        )}
      </article>

      {previewStatus === "idle" && !preview ? (
        <article className="card empty-panel">
          <EmptyState message={pageState.emptyMessage || "No completed forecast selected"} />
        </article>
      ) : null}
      {previewStatus === "loading" ? (
        <article className="card empty-panel">
          <LoadingState label="Loading report preview..." />
        </article>
      ) : null}
      {previewError ? <ErrorState message={previewError} /> : null}

      {pageState.showDownloads && preview ? (
        <>
          <DownloadBar
            disabled={Boolean(downloading)}
            onPdf={() => handleDownload("pdf")}
            onExcel={() => handleDownload("excel")}
            onOpenPdf={handleOpenPdf}
            downloadError={downloadError}
          />
          <ForecastReportPreview report={preview} />
          <DownloadBar
            disabled={Boolean(downloading)}
            onPdf={() => handleDownload("pdf")}
            onExcel={() => handleDownload("excel")}
            onOpenPdf={handleOpenPdf}
            downloadError={null}
          />
        </>
      ) : null}
    </section>
  );
}
