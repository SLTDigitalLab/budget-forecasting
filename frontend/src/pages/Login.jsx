import { useEffect } from "react";
import logo from "../assets/slt-mobitel-logo.svg";
import { useAuth } from "../auth/AuthContext";
import ErrorState from "../components/ErrorState";

function NextBudgetTitle({ id }) {
  return (
    <h1 id={id} className="login-title">
      <span className="login-title-next">Next</span>
      {" "}
      <span className="login-title-budget">Budget</span>
    </h1>
  );
}

function LoginModalWaves() {
  return (
    <div className="login-modal-waves" aria-hidden="true">
      <svg
        className="login-modal-waves-svg"
        viewBox="0 0 440 100"
        preserveAspectRatio="none"
        focusable="false"
      >
        <path
          className="login-wave-layer login-wave-layer--deep"
          d="M0 100 L0 6 C12 8 24 14 36 26 C58 50 78 70 104 84 C126 96 148 100 172 100 Z"
        />
        <path
          className="login-wave-layer login-wave-layer--mid"
          d="M0 100 L0 26 C16 28 30 36 42 48 C62 68 82 82 106 92 C124 98 142 100 162 100 Z"
        />
        <path
          className="login-wave-layer login-wave-layer--soft"
          d="M0 100 L0 46 C18 48 32 56 44 66 C64 82 84 92 108 97 C124 100 140 100 154 100 Z"
        />
        <path
          className="login-wave-layer login-wave-layer--deep"
          d="M440 100 L440 12 C424 10 406 18 390 32 C366 56 344 74 316 88 C296 96 278 100 254 100 Z"
        />
        <path
          className="login-wave-layer login-wave-layer--mid"
          d="M440 100 L440 32 C422 30 404 40 388 54 C366 72 344 86 318 94 C300 98 284 100 268 100 Z"
        />
        <path
          className="login-wave-layer login-wave-layer--soft"
          d="M440 100 L440 52 C422 50 404 58 388 70 C366 86 344 94 320 98 C304 100 290 100 278 100 Z"
        />
      </svg>
    </div>
  );
}

function LoginBrandFooter() {
  return (
    <footer className="login-brand-footer">
      <span className="login-brand-footer-rule" aria-hidden="true" />
      <img src={logo} alt="SLT-Mobitel" className="login-brand-footer-logo" width="160" height="48" />
      <span className="login-brand-footer-rule" aria-hidden="true" />
    </footer>
  );
}

export function ActionAuthenticationOverlay({ onSignIn, onCancel, busy = false, error = "" }) {
  return (
    <div className="settings-action-auth">
      <div className="login-scrim" />
      <div className="login-modal-layer">
        <article className="login-modal" role="dialog" aria-modal="true" aria-labelledby="settings-action-auth-title">
          <LoginModalWaves />
          <div className="login-hero">
            <p className="login-kicker">Welcome to</p>
            <NextBudgetTitle id="settings-action-auth-title" />
            <span className="login-title-underline" aria-hidden="true" />
            <p className="login-prompt">Please authenticate to make changes</p>
          </div>
          {error ? <ErrorState message={error} /> : null}
          <button type="button" className="login-microsoft-button" onClick={onSignIn} disabled={busy} aria-busy={busy}>
            <MicrosoftMark />
            <span>Sign in with Microsoft</span>
          </button>
          <button type="button" className="login-cancel-button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <LoginBrandFooter />
        </article>
      </div>
    </div>
  );
}

export function AuthenticationOverlay() {
  const { error, busy, login, retry } = useAuth();

  useEffect(() => {
    function blockDismiss(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
      }
    }
    document.addEventListener("keydown", blockDismiss, true);
    return () => document.removeEventListener("keydown", blockDismiss, true);
  }, []);

  return (
    <>
      <div className="login-scrim" aria-hidden="true" />
      <div className="login-modal-layer">
        <article
          className="login-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="login-title"
        >
          <LoginModalWaves />
          <div className="login-hero">
            <p className="login-kicker">Welcome to</p>
            <NextBudgetTitle id="login-title" />
            <span className="login-title-underline" aria-hidden="true" />
            <p className="login-prompt">Please authenticate to continue</p>
          </div>
          {error ? <ErrorState message={error} /> : null}
          <button
            type="button"
            className="login-microsoft-button"
            onClick={error ? retry : login}
            disabled={busy}
            aria-busy={busy}
          >
            <MicrosoftMark />
            <span>{error ? "Retry" : "Sign in with Microsoft"}</span>
          </button>
          <LoginBrandFooter />
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
