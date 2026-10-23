import { Menu, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { cancelSettingsAction, startSettingsActionRedirect, subscribeActionPrompt } from "../auth/actionAuthentication";
import { FILE_AUTH_CANCELLED } from "../utils/fileSelectionAuth";
import { ActionAuthenticationOverlay } from "../pages/Login";
import AppErrorBoundary from "./AppErrorBoundary";
import RetrainExperience from "./RetrainExperience";
import Sidebar from "./Sidebar";

const NARROW_QUERY = "(max-width: 860px)";

export default function AppLayout({ contentInert = false }) {
  const [open, setOpen] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [actionPrompt, setActionPrompt] = useState(null);
  const [actionError, setActionError] = useState("");
  const location = useLocation();
  const menuRef = useRef(null);
  const sidebarRef = useRef(null);
  const drawer = narrow && open;

  useEffect(() => subscribeActionPrompt((prompt) => {
    setActionPrompt(prompt);
    if (!prompt) {
      setActionError("");
    }
  }), []);

  useEffect(() => {
    const media = window.matchMedia(NARROW_QUERY);
    const apply = () => setNarrow(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!drawer) {
      return undefined;
    }
    const sidebar = sidebarRef.current;
    const previous = document.activeElement;
    const focusable = () => [...(sidebar?.querySelectorAll(
      "a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"
    ) || [])];
    focusable()[0]?.focus();
    function onKey(event) {
      if (event.defaultPrevented) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const items = focusable();
      if (!items.length) {
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (previous instanceof HTMLElement) {
        previous.focus();
      } else {
        menuRef.current?.focus();
      }
    };
  }, [drawer]);

  function signInForAction() {
    setActionError("");
    try {
      startSettingsActionRedirect({ descriptor: actionPrompt });
    } catch (cause) {
      setActionError(cause.message || "Microsoft sign-in could not be started.");
    }
  }

  return (
    <div className={`app-shell${drawer ? " nav-open" : ""}`}>
      {drawer ? (
        <button
          type="button"
          className="backdrop"
          aria-label="Close navigation"
          onClick={() => setOpen(false)}
        />
      ) : null}
      <Sidebar
        sidebarRef={sidebarRef}
        open={open}
        drawer={drawer}
        collapsed={narrow && !open}
        onNavigate={() => setOpen(false)}
        onClose={() => setOpen(false)}
      />
      <div className={`content${drawer ? " nav-locked" : ""}`}>
        <button
          ref={menuRef}
          type="button"
          className="menu-button generate-button"
          aria-label={open ? "Close navigation" : "Open navigation"}
          aria-expanded={open}
          aria-controls="app-navigation"
          onClick={() => setOpen((current) => !current)}
        >
          {open ? <X size={18} /> : <Menu size={18} />}
        </button>
        <div
          className="content-main"
          inert={contentInert || drawer ? "" : undefined}
          aria-hidden={contentInert || drawer ? "true" : undefined}
        >
          <AppErrorBoundary key={location.pathname}>
            <Outlet />
          </AppErrorBoundary>
        </div>
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
