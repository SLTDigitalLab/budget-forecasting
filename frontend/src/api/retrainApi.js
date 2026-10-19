import { failedResponseError } from "../utils/retrainWorkflow.js";

async function retrainRequest(path, options = {}) {
  const token = options.token || "";
  const headers = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw failedResponseError(response.status, payload);
  }
  return response.json();
}

export function getModelStatus() {
  return retrainRequest("/api/retraining/model-status", { method: "GET" });
}

export function getRetrainStatus(token) {
  return retrainRequest("/api/retraining/status", { method: "GET", token });
}

export function startRetrain(token) {
  return retrainRequest("/api/retraining/jobs", { method: "POST", token });
}

export function saveRetrain(jobId, token) {
  return retrainRequest(`/api/retraining/jobs/${jobId}/save`, { method: "POST", token });
}

export function cancelRetrain(jobId, token) {
  return retrainRequest(`/api/retraining/jobs/${jobId}/cancel`, { method: "POST", token });
}

export function retryRetrain(jobId, token) {
  return retrainRequest(`/api/retraining/jobs/${jobId}/retry`, { method: "POST", token });
}
