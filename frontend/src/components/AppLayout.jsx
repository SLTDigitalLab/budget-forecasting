import { Menu, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { cancelSettingsAction, startSettingsActionRedirect, subscribeActionPrompt } from "../auth/actionAuthentication";
import { FILE_AUTH_CANCELLED } from "../utils/fileSelectionAuth";
import { ActionAuthenticationOverlay } from "../pages/Login";
import AppErrorBoundary from "./AppErrorBoundary";
import RetrainExperience from "./RetrainExperience";
import Sidebar from "./Sidebar";

export default function AppLayout() {
  const [open, setOpen] = useState(false);
  const [actionPrompt, setActionPrompt] = useState(null);
  const [actionError, setActionError] = useState("");
  const location = useLocation();

  useEffect(() => subscribeActionPrompt((prompt) => {
    setActionPrompt(prompt);
    if (!prompt) {
      setActionError("");
    }
  }), []);

  function signInForAction() {
    setActionError("");
    try {
      startSettingsActionRedirect({ descriptor: actionPrompt });
    } catch (cause) {
      setActionError(cause.message || "Microsoft sign-in could not be started.");
    }
  }

  return (
    <div className="app-shell">
      {open ? (
        <button
          type="button"
          className="backdrop"
          aria-label="Close navigation"
          onClick={() => setOpen(false)}
        />
      ) : null}
      <Sidebar open={open} onNavigate={() => setOpen(false)} />
      <div className="content">
        <button
          type="button"
          className="menu-button generate-button"
          aria-label={open ? "Close navigation" : "Open navigation"}
          aria-expanded={open}
          onClick={() => setOpen((current) => !current)}
        >
          {open ? <X size={18} /> : <Menu size={18} />}
        </button>
        <AppErrorBoundary key={location.pathname}>
          <Outlet />
        </AppErrorBoundary>
      </div>
      <RetrainExperience />
      {actionPrompt ? (
        <ActionAuthenticationOverlay
          busy={false}
          error={actionError}
          onSignIn={signInForAction}
          onCancel={() => cancelSettingsAction(sessionStorage, FILE_AUTH_CANCELLED)}
        />
      ) : null}
    </div>
  );
}
