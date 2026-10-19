import { useAuth } from "../auth/AuthContext";
import ErrorState from "../components/ErrorState";
import LoadingState from "../components/LoadingState";

export function ActionAuthenticationOverlay({ onSignIn, onCancel, busy = false, error = "" }) {
  return (
    <div className="settings-action-auth">
      <div className="login-scrim" />
      <div className="login-modal-layer">
        <article className="login-modal" role="dialog" aria-modal="true" aria-labelledby="settings-action-auth-title">
          <h1 id="settings-action-auth-title" className="login-title">
            AI Budget Forecasting System
          </h1>
          <p className="login-prompt">Please authenticate to make changes</p>
          {error ? <ErrorState message={error} /> : null}
          <button type="button" className="login-microsoft-button" onClick={onSignIn} disabled={busy}>
            <MicrosoftMark />
            <span>Sign in with Microsoft</span>
          </button>
          <button type="button" className="login-cancel-button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </article>
      </div>
    </div>
  );
}

export function AuthenticationOverlay() {
  const { error, busy, login, retry } = useAuth();

  return (
    <>
      <div className="login-scrim" />
      <div className="login-modal-layer">
        <article className="login-modal" role="dialog" aria-modal="true" aria-labelledby="login-title">
          <p className="login-kicker">Welcome to</p>
          <h1 id="login-title" className="login-title">
            AI Budget Forecasting System
          </h1>
          <p className="login-prompt">Please authenticate to continue</p>
          {busy ? <LoadingState label="Contacting Microsoft..." /> : null}
          {error ? <ErrorState message={error} /> : null}
          <button
            type="button"
            className="login-microsoft-button"
            onClick={error ? retry : login}
            disabled={busy}
          >
            <MicrosoftMark />
            <span>{error ? "Retry" : "Sign in with Microsoft"}</span>
          </button>
        </article>
      </div>
    </>
  );
}

function MicrosoftMark() {
  return (
    <svg className="login-microsoft-mark" width="18" height="18" viewBox="0 0 21 21" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}
