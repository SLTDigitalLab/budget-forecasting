import { ChevronUp, LogOut, User } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { profileEmail, profileInitial, profileName } from "../utils/sidebarProfile";

function Avatar({ user, size = 40, className = "sidebar-profile-avatar" }) {
  const initial = profileInitial(user);
  return (
    <span className={className} style={{ width: size, height: size }} aria-hidden="true">
      {initial ? initial : <User size={size === 40 ? 20 : 22} strokeWidth={2.2} />}
    </span>
  );
}

function AccountPanel({ user, panelId, labelledBy, panelRef, style, onLogout }) {
  const name = profileName(user);
  const email = profileEmail(user);
  return (
    <div
      ref={panelRef}
      id={panelId}
      className="sidebar-account-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      tabIndex={-1}
      style={style}
    >
      <div className="sidebar-account-header">
        <Avatar user={user} size={48} className="sidebar-profile-avatar sidebar-account-avatar" />
        <div className="sidebar-account-copy">
          <p id={labelledBy} className="sidebar-account-name">
            {name || "Signed-in user"}
          </p>
          {email ? <p className="sidebar-account-email">{email}</p> : null}
        </div>
      </div>
      <div className="sidebar-account-divider" />
      <button type="button" className="sidebar-account-logout" onClick={onLogout}>
        <LogOut size={18} aria-hidden="true" />
        Logout
      </button>
    </div>
  );
}

export default function SidebarProfile({ user, onLogout }) {
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 240 });
  const panelId = useId();
  const nameId = useId();
  const name = profileName(user);
  const email = profileEmail(user);
  const primary = name || email || "Signed-in user";
  const secondary = name ? email : "";

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) {
      triggerRef.current?.focus();
    }
  }, []);

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const panel = panelRef.current;
    if (!trigger) {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    const gap = 8;
    const margin = 8;
    const width = Math.min(Math.max(rect.width, 240), window.innerWidth - margin * 2);
    const height = panel?.offsetHeight || 168;
    let left = rect.left;
    if (left + width > window.innerWidth - margin) {
      left = window.innerWidth - width - margin;
    }
    if (left < margin) {
      left = margin;
    }
    let top = rect.top - height - gap;
    if (top < margin) {
      const below = rect.bottom + gap;
      top = below + height <= window.innerHeight - margin ? below : margin;
    }
    setCoords({ top, left, width });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      return undefined;
    }
    updatePosition();
    const frame = requestAnimationFrame(updatePosition);
    return () => cancelAnimationFrame(frame);
  }, [open, updatePosition, name, email]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        close(true);
      }
    }
    function onPointerDown(event) {
      const target = event.target;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) {
        return;
      }
      close(true);
    }
    function onReposition() {
      updatePosition();
    }
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("resize", onReposition);
    window.addEventListener("scroll", onReposition, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("resize", onReposition);
      window.removeEventListener("scroll", onReposition, true);
    };
  }, [open, close, updatePosition]);

  useEffect(() => {
    if (open) {
      panelRef.current?.focus();
    }
  }, [open]);

  function toggle() {
    setOpen((current) => !current);
  }

  function handleLogout() {
    close(false);
    onLogout();
  }

  return (
    <div className="sidebar-profile">
      <button
        ref={triggerRef}
        type="button"
        className={`sidebar-profile-card${open ? " is-open" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={toggle}
      >
        <Avatar user={user} />
        <span className="sidebar-profile-copy">
          <span className="sidebar-profile-name">{primary}</span>
          {secondary ? <span className="sidebar-profile-email">{secondary}</span> : null}
        </span>
        <ChevronUp size={16} className="sidebar-profile-chevron" aria-hidden="true" />
      </button>
      {open
        ? createPortal(
            <AccountPanel
              user={user}
              panelId={panelId}
              labelledBy={nameId}
              panelRef={panelRef}
              style={{ top: coords.top, left: coords.left, width: coords.width }}
              onLogout={handleLogout}
            />,
            document.body
          )
        : null}
    </div>
  );
}
