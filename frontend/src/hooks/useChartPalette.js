import { useMemo } from "react";
import { useTheme } from "../context/ThemeContext";
import { getChartPalette } from "../utils/chartLayout";

/** Chart colors that update when the app theme changes; no data refetch. */
export default function useChartPalette() {
  const { theme } = useTheme();
  return useMemo(() => getChartPalette(), [theme]);
}
