import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import LoadingState from "../components/LoadingState";
import { consumeAuthReturn, rememberAuthReturn } from "./authRouting.js";
import { cancelSettingsAction, rememberReturnedActionToken } from "./actionAuthentication.js";
import { resetFileSelectionAuthorization } from "../utils/fileSelectionAuth.js";
import {
  clearLoginSession,
  consumeOAuthCallback,
  displayNameFromUser,
  startMicrosoftLogin,
} from "./microsoftLogin.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const navigate = useNavigate();
  const [user, setUser] = useState(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    consumeOAuthCallback().then((result) => {
      if (!live) {
        return;
      }
      if (result.actionAttempt && result.actionAccessToken) {
        rememberReturnedActionToken(result.actionAccessToken, result.actionExpiresIn);
      } else if (result.actionAttempt) {
        cancelSettingsAction(sessionStorage, result.error || "Microsoft sign-in was cancelled. Retry when you are ready to continue.");
      }
      setUser(result.user);
      setError(result.actionAttempt ? "" : result.error || "");
      setReady(true);
      if (result.user) {
        const returnTo = consumeAuthReturn(sessionStorage);
        if (returnTo) {
          navigate(returnTo, { replace: true });
        }
      }
    });
    return () => {
      live = false;
    };
  }, [navigate]);

  const login = useCallback(() => {
    setError("");
    setBusy(true);
    try {
      rememberAuthReturn(window.location.pathname, sessionStorage);
      startMicrosoftLogin();
    } catch (cause) {
      setBusy(false);
      setError(String(cause?.message || cause || "Microsoft sign-in could not be started."));
    }
  }, []);

  const logout = useCallback(() => {
    clearLoginSession();
    resetFileSelectionAuthorization();
    setUser(null);
    setError("");
  }, []);

  const retry = useCallback(() => {
    login();
  }, [login]);

  const value = useMemo(
    () => ({
      user,
      error,
      ready,
      busy,
      isAuthenticated: Boolean(user),
      displayName: displayNameFromUser(user),
      login,
      logout,
      retry,
    }),
    [busy, error, login, logout, ready, retry, user]
  );

  if (!ready) {
    return <LoadingState label="Checking Microsoft sign-in..." />;
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return value;
}
