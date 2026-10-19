import { Moon, Sun } from "lucide-react";
import { useTheme } from "../context/ThemeContext";

export default function ThemeToggle({ className = "" }) {
  const { theme, toggle, actionLabel } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      type="button"
      className={`theme-toggle theme-toggle-icon${className ? ` ${className}` : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        toggle();
      }}
      aria-label={actionLabel}
      title={actionLabel}
    >
      {isDark ? <Sun size={18} strokeWidth={1.75} aria-hidden="true" /> : <Moon size={18} strokeWidth={1.75} aria-hidden="true" />}
    </button>
  );
}
