import { useEffect, useRef, useState } from "react";
import { claimReturnedActionToken, consumeSettingsActionResume, requestSettingsAction, subscribeActionPrompt } from "../auth/actionAuthentication";
import { cancelRetrain, getRetrainStatus, retryRetrain, saveRetrain } from "../api/retrainApi";
import {
  LOCK_MESSAGE,
  READY_TEXT,
  SAVED_TEXT,
  backdropDismisses,
  blocksNavigation,
  countdownSeconds,
  escapeDismisses,
  progressText,
  restoreRetrainPresentation,
  shouldStickToBottom,
  showSaveFailureActions,
  showSaveModels,
  showTrainingFailureActions,
} from "../utils/retrainWorkflow";

export default function RetrainExperience() {
  const [status, setStatus] = useState(null);
  const [fetchedAt, setFetchedAt] = useState(() => Date.now());
  const [dismissedJobId, setDismissedJobId] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [actionError, setActionError] = useState("");
  const [working, setWorking] = useState(false);
  const logRef = useRef(null);
  const stickToBottom = useRef(true);
  const actionGuard = useRef({ pending: false });

  useEffect(() => {
    let timer = 0;
    let stop = false;

    async function tick() {
      try {
        const next = await getRetrainStatus();
        if (!stop) {
          setStatus(next);
          setFetchedAt(Date.now());
        }
      } catch {
        // A failed status read does not invent a system lock.
      }
      if (!stop) {
        timer = window.setTimeout(tick, 2000);
      }
    }

    tick();
    function onSession() {
      tick();
    }
    window.addEventListener("retrain-session", onSession);
    return () => {
      stop = true;
      window.clearTimeout(timer);
      window.removeEventListener("retrain-session", onSession);
    };
  }, []);

  useEffect(() => {
    const node = logRef.current;
    if (node && stickToBottom.current) {
      node.scrollTop = node.scrollHeight;
    }
  }, [status?.logs]);

  const presentation = restoreRetrainPresentation(status, dismissedJobId, now, fetchedAt);
  const dismissible = presentation.dismissible && escapeDismisses(status?.state);

  useEffect(() => {
    if (presentation.mode !== "modal") {
      return undefined;
    }
    function onKey(event) {
      if (event.key !== "Escape") {
        return;
      }
      if (!escapeDismisses(status?.state)) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      setDismissedJobId(status?.job_id || "");
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [presentation.mode, status?.state, status?.job_id]);

  useEffect(() => {
    if (status?.state !== "SAVED") {
      return undefined;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [status?.state, status?.unlock_at]);

  useEffect(() => subscribeActionPrompt((prompt) => {
    if (!prompt) {
      actionGuard.current.pending = false;
      setWorking(false);
    }
  }), []);

  const RETRAIN_ACTION_KINDS = new Set(["save-models", "retry-save", "cancel-job", "retry-training"]);

  useEffect(() => {
    const outcome = consumeSettingsActionResume(undefined, RETRAIN_ACTION_KINDS);
    if (!outcome || outcome.mode !== "run") {
      return;
    }
    const payload = outcome.resume.payload || {};
    const runners = {
      "save-models": (token) => saveRetrain(payload.jobId, token),
      "retry-save": (token) => saveRetrain(payload.jobId, token),
      "cancel-job": (token) => cancelRetrain(payload.jobId, token),
      "retry-training": (token) => retryRetrain(payload.jobId, token),
    };
    const action = runners[outcome.resume.kind];
    if (action) {
      runAction(outcome.resume.kind, payload.jobId, action);
    } else {
      claimReturnedActionToken();
    }
  }, []);

  function runAction(kind, jobId, action) {
    const token = claimReturnedActionToken();
    if (!token) {
      if (actionGuard.current.pending) {
        return;
      }
      actionGuard.current.pending = true;
      setWorking(true);
      requestSettingsAction({ kind, payload: { jobId }, destructive: true });
      return;
    }
    setWorking(true);
    setActionError("");
    Promise.resolve()
      .then(() => action(token))
      .then(async () => {
        const next = await getRetrainStatus();
        setStatus(next);
        setFetchedAt(Date.now());
        setNow(Date.now());
      })
      .catch(async (cause) => {
        setActionError(cause.message || "The retraining action failed.");
        try {
          const next = await getRetrainStatus();
          setStatus(next);
          setFetchedAt(Date.now());
        } catch {
          // Keep the action error visible.
        }
      })
      .finally(() => setWorking(false));
  }

  if (presentation.mode === "hidden") {
    return null;
  }

  if (presentation.mode === "locked") {
    return (
      <div className="system-lock-layer" role="alertdialog" aria-modal="true" aria-label={LOCK_MESSAGE}>
        <section className="card system-lock-card">
          <h2>{LOCK_MESSAGE}</h2>
          {actionError ? <p className="retrain-error">{actionError}</p> : null}
        </section>
      </div>
    );
  }

  const remaining = countdownSeconds(status, now, fetchedAt);
  const progress = progressText(status);
  const navigationBlocked = blocksNavigation(status?.state);

  return (
    <div className="system-lock-layer" role="presentation" data-backdrop-dismiss={backdropDismisses() ? "true" : "false"}>
      <section
        className="card retrain-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="retrain-modal-title"
        data-navigation-blocked={navigationBlocked ? "true" : "false"}
        data-dismissible={dismissible ? "true" : "false"}
      >
        <h2 id="retrain-modal-title">Retrain Models</h2>
        <p className="retrain-stage">{progress}</p>
        {status?.state === "READY_TO_SAVE" ? <p className="settings-success">{READY_TEXT}</p> : null}
        {status?.state === "SAVED" ? (
          <p className="settings-success">
            {SAVED_TEXT}
            {remaining != null ? ` Closing in ${remaining} seconds.` : null}
          </p>
        ) : null}
        {status?.cache_warning ? <p className="muted">{status.cache_warning}</p> : null}
        {status?.error_detail ? <p className="retrain-error">{status.error_detail}</p> : null}
        {actionError ? <p className="retrain-error">{actionError}</p> : null}
        <pre
          ref={logRef}
          className="retrain-terminal"
          aria-live="polite"
          onScroll={(event) => {
            const node = event.currentTarget;
            stickToBottom.current = shouldStickToBottom(node.scrollHeight, node.scrollTop, node.clientHeight);
          }}
        >
          {status?.logs || ""}
        </pre>
        <div className="retrain-actions">
          {showSaveModels(status?.state) ? (
            <button type="button" className="generate-button" disabled={working} onClick={() => runAction("save-models", status.job_id, (token) => saveRetrain(status.job_id, token))}>
              Save Models
            </button>
          ) : null}
          {showSaveFailureActions(status?.state) ? (
            <>
              <button type="button" className="generate-button" disabled={working} onClick={() => runAction("retry-save", status.job_id, (token) => saveRetrain(status.job_id, token))}>
                Retry Save
              </button>
              <button type="button" className="generate-button secondary" disabled={working} onClick={() => runAction("cancel-job", status.job_id, (token) => cancelRetrain(status.job_id, token))}>
                Cancel
              </button>
            </>
          ) : null}
          {showTrainingFailureActions(status?.state) ? (
            <>
              <button type="button" className="generate-button" disabled={working} onClick={() => runAction("retry-training", status.job_id, (token) => retryRetrain(status.job_id, token))}>
                Retry training
              </button>
              <button type="button" className="generate-button secondary" onClick={() => setDismissedJobId(status.job_id)}>
                Close
              </button>
            </>
          ) : null}
        </div>
      </section>
    </div>
  );
}
