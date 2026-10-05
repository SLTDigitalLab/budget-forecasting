import { useLocation } from "react-router-dom";
import LoadingState from "../components/LoadingState";
import AppLayout from "../components/AppLayout";
import { ForecastProvider } from "../context/ForecastContext";
import { AuthenticationOverlay } from "../pages/Login";
import { useAuth } from "./AuthContext";
import { pageFrameFor, shouldMountProtectedPage } from "./authRouting";

export default function ProtectedRoute() {
  const { isAuthenticated, ready } = useAuth();

  if (!ready) {
    return <LoadingState label="Checking Microsoft sign-in..." />;
  }

  const signedOut = !shouldMountProtectedPage(isAuthenticated);

  return (
    <ForecastProvider enabled={isAuthenticated}>
      <div className={signedOut ? "auth-frame is-locked" : "auth-frame"}>
        <div className={signedOut ? "login-app-preview" : undefined} inert={signedOut ? "" : undefined} aria-hidden={signedOut ? "true" : undefined}>
          <AppLayout />
        </div>
        {signedOut ? <AuthenticationOverlay /> : null}
      </div>
    </ForecastProvider>
  );
}

export function GuardedPage({ children }) {
  const { isAuthenticated } = useAuth();
  const { pathname } = useLocation();
  if (!shouldMountProtectedPage(isAuthenticated)) {
    const frame = pageFrameFor(pathname);
    return (
      <section>
        <header className="page-header">
          <div>
            <h1>{frame.title}</h1>
            {frame.summary ? <p>{frame.summary}</p> : null}
          </div>
        </header>
      </section>
    );
  }
  return children;
}
