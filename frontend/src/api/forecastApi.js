import { failedResponseError } from "../utils/retrainWorkflow.js";

async function readError(response) {
  try {
    return failedResponseError(response.status, await response.json());
  } catch {
    return failedResponseError(response.status, null);
  }
}

export async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    throw await readError(response);
  }
  return response.json();
}

export function getHealth() {
  return request("/api/health");
}

export function generateForecast(payload) {
  return request("/api/forecasting/generate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getOverviewCategories() {
  return request("/api/overview/categories");
}

export function getForecastAccounts() {
  return request("/api/forecasting/accounts");
}

export function getOverviewHistorical(category) {
  const query = category ? `?category=${encodeURIComponent(category)}` : "";
  return request(`/api/overview/historical${query}`);
}

export function getOverviewAnalysis(category) {
  const query = category ? `?category=${encodeURIComponent(category)}` : "";
  return request(`/api/overview/analysis${query}`);
}

export function getOverviewForecastActivity() {
  return request("/api/overview/forecast-activity");
}

export function getForecastHistory(limit = 50) {
  return request(`/api/forecasting/history?limit=${encodeURIComponent(limit)}`);
}

export function getForecastRecord(runId) {
  return request(`/api/forecasting/history/${encodeURIComponent(runId)}`);
}

function filenameFromDisposition(header, fallback) {
  const text = String(header || "");
  const matched = text.match(/filename="([^"]+)"/i);
  return matched?.[1] || fallback;
}

async function downloadBinary(path, fallbackName) {
  const response = await fetch(path);
  if (!response.ok) {
    throw await readError(response);
  }
  const blob = await response.blob();
  const filename = filenameFromDisposition(response.headers.get("Content-Disposition"), fallbackName);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return filename;
}

export function downloadForecastPdf(runId, filename) {
  return downloadBinary(`/api/forecasting/reports/${encodeURIComponent(runId)}/pdf`, filename);
}

export function downloadForecastExcel(runId, filename) {
  return downloadBinary(`/api/forecasting/reports/${encodeURIComponent(runId)}/excel`, filename);
}

export function forecastPdfPreviewUrl(runId) {
  return `/api/forecasting/reports/${encodeURIComponent(runId)}/pdf?inline=true`;
}

export async function getLatestForecastRecord() {
  const response = await fetch("/api/forecasting/history/latest", {
    headers: {
      "Content-Type": "application/json",
    },
  });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw await readError(response);
  }
  return response.json();
}
