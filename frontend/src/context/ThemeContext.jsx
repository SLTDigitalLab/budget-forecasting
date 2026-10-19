import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  applyTheme,
  resolveInitialTheme,
  themeActionLabel,
  toggleTheme,
  writeStoredTheme,
} from "../utils/theme";

const ThemeContext = createContext({
  theme: "light",
  setTheme: () => {},
  toggle: () => {},
  actionLabel: "Switch to dark mode",
});

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => applyTheme(resolveInitialTheme()));

  const setTheme = useCallback((next) => {
    const applied = applyTheme(next);
    writeStoredTheme(applied);
    setThemeState(applied);
  }, []);

  const toggle = useCallback(() => {
    setTheme(toggleTheme(theme));
  }, [setTheme, theme]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const value = useMemo(
    () => ({
      theme,
      setTheme,
      toggle,
      actionLabel: themeActionLabel(theme),
    }),
    [theme, setTheme, toggle]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
