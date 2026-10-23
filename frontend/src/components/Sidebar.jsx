import { X } from "lucide-react";
import {
  BarChart3,
  Download,
  History,
  LayoutDashboard,
  Settings,
  TrendingUp,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useForecast } from "../context/ForecastContext";
import symbol from "../assets/slt-symbol.svg";
import SidebarProfile from "./SidebarProfile";
import ThemeToggle from "./ThemeToggle";

const items = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/generate-forecast", label: "Generate Forecast", icon: TrendingUp },
  { to: "/analytics", label: "Analytics & Charts", icon: BarChart3 },
  { to: "/export", label: "Export Forecasts", icon: Download },
  { to: "/settings", label: "Settings", icon: Settings },
  { to: "/forecast-history", label: "Forecast History", icon: History },
];

export default function Sidebar({ sidebarRef, open, drawer = false, collapsed = false, onNavigate, onClose }) {
  const { user, logout } = useAuth();
  const { resetSession } = useForecast();

  function handleLogout() {
    resetSession();
    logout();
  }

  return (
    <aside
      ref={sidebarRef}
      id="app-navigation"
      className={`sidebar ${open ? "open" : ""}`}
      aria-label="Main navigation"
      role={drawer ? "dialog" : undefined}
      aria-modal={drawer ? "true" : undefined}
      aria-hidden={collapsed ? "true" : undefined}
      inert={collapsed ? "" : undefined}
    >
      <div className="brand">
        <div className="brand-lockup">
          <div className="brand-logo-frame">
            <img src={symbol} alt="" className="brand-logo" width="36" height="36" />
          </div>
          <div className="brand-copy">
            <h1 className="brand-title">SLT-Mobitel</h1>
            <p className="brand-subtitle">AI Budget Forecasting System</p>
          </div>
        </div>
        <button
          type="button"
          className="sidebar-close"
          data-drawer-close="true"
          aria-label="Close navigation"
          onClick={onClose}
        >
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <nav className="nav-list">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              onClick={onNavigate}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <SidebarProfile user={user} onLogout={handleLogout} />
        <ThemeToggle />
      </div>
    </aside>
  );
}
