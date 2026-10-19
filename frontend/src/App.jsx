import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { getAuthConfigError } from "./auth/authConfig";
import ProtectedRoute, { GuardedPage } from "./auth/ProtectedRoute";
import AuthStartupError from "./components/AuthStartupError";
import Analytics from "./pages/Analytics";
import ForecastHistory from "./pages/ForecastHistory";
import GenerateForecast from "./pages/GenerateForecast";
import Overview from "./pages/Overview";
import ExportForecasts from "./pages/ExportForecasts";
import Settings from "./pages/Settings";

export default function App() {
  const configError = getAuthConfigError();
  if (configError) {
    return <AuthStartupError message={configError} />;
  }

  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route element={<ProtectedRoute />}>
            <Route index element={<GuardedPage><Overview /></GuardedPage>} />
            <Route path="generate-forecast" element={<GuardedPage><GenerateForecast /></GuardedPage>} />
            <Route path="analytics" element={<GuardedPage><Analytics /></GuardedPage>} />
            <Route path="export" element={<GuardedPage><ExportForecasts /></GuardedPage>} />
            <Route path="settings" element={<GuardedPage><Settings /></GuardedPage>} />
            <Route path="forecast-history" element={<GuardedPage><ForecastHistory /></GuardedPage>} />
            <Route path="activity-logs" element={<Navigate to="/forecast-history" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
