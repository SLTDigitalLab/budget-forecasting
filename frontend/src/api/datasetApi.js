import { failedResponseError } from "../utils/retrainWorkflow.js";

async function readError(response) {
  try {
    return failedResponseError(response.status, await response.json());
  } catch {
    return failedResponseError(response.status, null);
  }
}

async function datasetRequest(path, options = {}) {
  const headers = {
    ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    throw await readError(response);
  }
  if (response.status === 204) {
    return null;
  }
  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response;
}

export function getDatasetSummary() {
  return datasetRequest("/api/datasets/summary");
}

export function assertEditorAccess(token) {
  return datasetRequest("/api/datasets/editor-access", { method: "GET", token });
}

export function previewDataset(file, sheetName, token) {
  const body = new FormData();
  body.append("file", file);
  if (sheetName) {
    body.append("sheet_name", sheetName);
  }
  return datasetRequest("/api/datasets/preview", { method: "POST", body, token });
}

export function confirmDataset(previewId, token) {
  return datasetRequest("/api/datasets/confirm", {
    method: "POST",
    token,
    body: JSON.stringify({ preview_id: previewId }),
  });
}

export function stageDatasetPreview(previewId, body, token) {
  return datasetRequest(`/api/datasets/previews/${encodeURIComponent(previewId)}/stage`, {
    method: "POST",
    token,
    body: JSON.stringify(body),
  });
}

export function getMasterDataset(params = {}) {
  const query = new URLSearchParams();
  query.set("page", String(params.page || 1));
  query.set("page_size", String(params.pageSize || 20));
  if (params.search) query.set("search", params.search);
  if (params.revisionFilter) query.set("revision_filter", params.revisionFilter);
  if (params.highlight) query.set("highlight", params.highlight);
  return datasetRequest(`/api/datasets/master?${query.toString()}`);
}

export function previewMasterEdits(edits, masterVersion, token) {
  return datasetRequest("/api/datasets/master/preview", {
    method: "POST",
    token,
    body: JSON.stringify({ edits, master_version: masterVersion }),
  });
}

export function confirmMasterEdits(edits, masterVersion, token) {
  return datasetRequest("/api/datasets/master/confirm", {
    method: "POST",
    token,
    body: JSON.stringify({ edits, master_version: masterVersion }),
  });
}

export function listRevisions(page = 1, pageSize = 10) {
  return datasetRequest(`/api/datasets/revisions?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`);
}

export function viewRevision(revisionId) {
  return datasetRequest(`/api/datasets/revisions/${encodeURIComponent(revisionId)}`);
}

export function previewRollback(revisionId, reason, token) {
  return datasetRequest(`/api/datasets/revisions/${encodeURIComponent(revisionId)}/rollback/preview`, {
    method: "POST",
    token,
    body: JSON.stringify({ reason }),
  });
}

export function confirmRollback(revisionId, reason, masterVersion, token) {
  return datasetRequest(`/api/datasets/revisions/${encodeURIComponent(revisionId)}/rollback/confirm`, {
    method: "POST",
    token,
    body: JSON.stringify({ reason, master_version: masterVersion }),
  });
}

export function listDatasetFiles(page = 1, pageSize = 10) {
  return datasetRequest(`/api/datasets/files?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`);
}

export function viewDatasetFile(fileId, search = "") {
  const query = search ? `?search=${encodeURIComponent(search)}` : "";
  return datasetRequest(`/api/datasets/files/${encodeURIComponent(fileId)}${query}`);
}

export async function downloadDatasetFile(fileId, fallbackName) {
  const response = await datasetRequest(`/api/datasets/files/${encodeURIComponent(fileId)}/download`);
  const blob = await response.blob();
  const header = response.headers.get("Content-Disposition") || "";
  const matched = header.match(/filename="([^"]+)"/i);
  const filename = matched?.[1] || fallbackName || "dataset.xlsx";
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

export function saveDatasetEdits(fileId, payload, token) {
  return datasetRequest(`/api/datasets/files/${encodeURIComponent(fileId)}/edits`, {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}
