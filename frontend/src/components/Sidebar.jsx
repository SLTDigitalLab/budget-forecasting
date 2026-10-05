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

const items = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/generate-forecast", label: "Generate Forecast", icon: TrendingUp },
  { to: "/analytics", label: "Analytics & Charts", icon: BarChart3 },
  { to: "/export", label: "Export Forecasts", icon: Download },
  { to: "/settings", label: "Settings", icon: Settings },
  { to: "/forecast-history", label: "Forecast History", icon: History },
];

export default function Sidebar({ open, onNavigate }) {
  const { user, logout } = useAuth();
  const { resetSession } = useForecast();

  function handleLogout() {
    resetSession();
    logout();
  }

  return (
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Main navigation">
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
      <SidebarProfile user={user} onLogout={handleLogout} />
    </aside>
  );
}
