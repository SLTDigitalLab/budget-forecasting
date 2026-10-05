import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { generateForecast, getHealth } from "../api/forecastApi";
import { ALL_CATEGORIES_ID, ALL_CATEGORIES_LABEL, requestCategoryValue } from "../utils/forecastCategory";
import { inclusiveMonthCount } from "../utils/months";
import {
  MAX_FORECAST_END_ISO,
  MAX_FORECAST_MONTHS,
  PERIOD_TYPE,
  defaultEndIso,
  firstValidForecastIso,
  validatePeriodTypeSelection,
} from "../utils/forecastPeriod";

const ForecastContext = createContext(null);

export function ForecastProvider({ children, enabled = true }) {
  const [health, setHealth] = useState(null);
  const [periodType, setPeriodType] = useState(PERIOD_TYPE.REMAINING_YEAR);
  const [category, setCategory] = useState({ id: ALL_CATEGORIES_ID, name: ALL_CATEGORIES_LABEL });
  const [startMonth, setStartMonth] = useState("2026-07");
  const [endMonth, setEndMonth] = useState("2026-12");
  const [forecast, setForecast] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [validation, setValidation] = useState("");
  const requestIdRef = useRef(0);

  const earliestMonth = useMemo(
    () => firstValidForecastIso(health?.history_end),
    [health]
  );

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    let cancelled = false;
    async function bootstrap() {
      try {
        const healthResponse = await getHealth();
        if (cancelled) {
          return;
        }
        setHealth(healthResponse);
        setError("");
        const nextEarliest = firstValidForecastIso(healthResponse.history_end);
        setPeriodType(PERIOD_TYPE.REMAINING_YEAR);
        setStartMonth(nextEarliest);
        setEndMonth(defaultEndIso(nextEarliest));
      } catch (cause) {
        if (!cancelled) {
          setError(cause.message || "Unable to reach the forecast service.");
        }
      }
    }
    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  const validate = useCallback(() => {
    const periodMessage = validatePeriodTypeSelection({
      periodType,
      startIso: startMonth,
      endIso: endMonth,
      earliestIso: earliestMonth,
      historyEnd: health?.history_end,
    });
    if (periodMessage) {
      return periodMessage;
    }
    const selectedSpan = inclusiveMonthCount(startMonth, endMonth);
    const horizonFromOrigin = inclusiveMonthCount(earliestMonth, endMonth);
    if (selectedSpan < 1) {
      return "Select at least one forecast month.";
    }
    if (horizonFromOrigin > MAX_FORECAST_MONTHS) {
      return `The selected range exceeds the ${MAX_FORECAST_MONTHS}-month forecast limit.`;
    }
    if (endMonth > MAX_FORECAST_END_ISO || startMonth > MAX_FORECAST_END_ISO) {
      return "Forecast dates after December 2030 are not supported.";
    }
    return "";
  }, [earliestMonth, endMonth, health?.history_end, periodType, startMonth]);

  const canSubmit = !validate();

  const runForecast = useCallback(async () => {
    const message = validate();
    setValidation(message);
    if (message) {
      return;
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setError("");
    try {
      const result = await generateForecast({
        start_month: startMonth,
        end_month: endMonth,
        category: requestCategoryValue(category),
      });
      if (requestId === requestIdRef.current) {
        setForecast(result);
      }
    } catch (cause) {
      if (requestId === requestIdRef.current) {
        const raw = cause.message || "Unable to generate the forecast.";
        const unsafe = /pickle|traceback|\\|\/app\/|best_monthly|best_model/i.test(raw);
        setError(unsafe ? "Unable to generate the forecast." : raw);
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [category, endMonth, startMonth, validate]);

  const applyForecast = useCallback((result) => {
    setForecast(result);
    setError("");
    setValidation("");
  }, []);

  const resetSession = useCallback(() => {
    requestIdRef.current += 1;
    setForecast(null);
    setError("");
    setValidation("");
    setLoading(false);
  }, []);

  const value = {
    health,
    category,
    setCategory,
    periodType,
    setPeriodType,
    startMonth,
    setStartMonth,
    endMonth,
    setEndMonth,
    earliestMonth,
    forecast,
    loading,
    error,
    validation,
    canSubmit,
    runForecast,
    applyForecast,
    resetSession,
  };

  return <ForecastContext.Provider value={value}>{children}</ForecastContext.Provider>;
}

export function useForecast() {
  const value = useContext(ForecastContext);
  if (!value) {
    throw new Error("useForecast must be used within ForecastProvider");
  }
  return value;
}
