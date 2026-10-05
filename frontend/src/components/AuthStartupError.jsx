import logo from "../assets/slt-mobitel-logo.svg";
import ErrorState from "./ErrorState";

export function retryFromOrigin() {
  window.location.replace(window.location.origin);
}

export default function AuthStartupError({ message }) {
  return (
    <main className="auth-startup-page">
      <article className="auth-startup-card">
        <div className="auth-startup-brand">
          <div className="login-logo-frame">
            <img src={logo} alt="SLT-Mobitel" className="login-logo" width="160" height="48" />
          </div>
          <h1>AI Budget Forecasting System</h1>
          <p>Microsoft sign-in could not finish. You can retry without leaving this app.</p>
        </div>
        <ErrorState message={message} />
        <button type="button" className="generate-button" onClick={retryFromOrigin}>
          Retry
        </button>
      </article>
    </main>
  );
}
