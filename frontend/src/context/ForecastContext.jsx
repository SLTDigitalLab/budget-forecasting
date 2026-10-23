import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { generateForecast, getHealth } from "../api/forecastApi";
import { ALL_CATEGORIES_ID, ALL_CATEGORIES_LABEL, requestCategoryValue } from "../utils/forecastCategory";
import { inclusiveMonthCount } from "../utils/months";
import {
  MAX_FORECAST_MONTHS,
  PERIOD_TYPE,
  monthYearPlain,
  remainingMonthsFromPublishedModel,
  resolveForecastPeriod,
  validatePeriodTypeSelection,
} from "../utils/forecastPeriod";

const ForecastContext = createContext(null);

export function ForecastProvider({ children, enabled = true }) {
  const [health, setHealth] = useState(null);
  const [boundsLoading, setBoundsLoading] = useState(enabled);
  const [boundsError, setBoundsError] = useState("");
  const [periodType, setPeriodType] = useState(PERIOD_TYPE.REMAINING_YEAR);
  const [category, setCategory] = useState({ id: ALL_CATEGORIES_ID, name: ALL_CATEGORIES_LABEL });
  const [startMonth, setStartMonth] = useState("");
  const [endMonth, setEndMonth] = useState("");
  const [forecast, setForecast] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [validation, setValidation] = useState("");
  const requestIdRef = useRef(0);
  const selectionRef = useRef({ startMonth: "", endMonth: "", periodType: PERIOD_TYPE.REMAINING_YEAR });
  selectionRef.current = { startMonth, endMonth, periodType };

  const earliestMonth = health?.min_forecast_month || "";
  const maximumMonth = health?.max_forecast_end || "";
  const historyEnd = /^\d{4}-\d{2}$/.test(health?.history_end || "") ? health.history_end : "";

  const applyDefaultPeriod = useCallback((minimum, maximum, publishedHistoryEnd, preferredType, publishedBoundsError) => {
    const remaining = remainingMonthsFromPublishedModel({
      historyEnd: publishedHistoryEnd,
      earliestIso: minimum,
      maximumIso: maximum,
      boundsError: publishedBoundsError,
    });
    let nextType = preferredType || PERIOD_TYPE.REMAINING_YEAR;
    if (nextType === PERIOD_TYPE.REMAINING_YEAR && !remaining.available) {
      nextType = PERIOD_TYPE.FINANCIAL_YEARS;
    }
    const resolved = resolveForecastPeriod({
      periodType: nextType,
      earliestIso: minimum,
      maximumIso: maximum,
      historyEnd: publishedHistoryEnd,
      boundsError: publishedBoundsError,
    });
    setPeriodType(nextType);
    setStartMonth(resolved.startIso || "");
    setEndMonth(resolved.endIso || "");
    if (nextType === PERIOD_TYPE.REMAINING_YEAR && !remaining.available) {
      setValidation(remaining.message);
    } else {
      setValidation("");
    }
  }, []);

  const refreshBounds = useCallback(async () => {
    if (!enabled) {
      setBoundsLoading(false);
      return;
    }
    setBoundsLoading(true);
    try {
      const healthResponse = await getHealth();
      const minimum = /^\d{4}-\d{2}$/.test(healthResponse.min_forecast_month || "")
        ? healthResponse.min_forecast_month
        : "";
      const maximum = /^\d{4}-\d{2}$/.test(healthResponse.max_forecast_end || "")
        ? healthResponse.max_forecast_end
        : "";
      const publishedHistoryEnd = /^\d{4}-\d{2}$/.test(healthResponse.history_end || "")
        ? healthResponse.history_end
        : "";
      const problem = healthResponse.forecast_bounds_error
        || (!healthResponse.model_loaded
          ? "The forecast model is not loaded."
          : (!minimum || !maximum || !publishedHistoryEnd
            ? "The published model did not provide a forecast period."
            : ""));
      setHealth({
        ...healthResponse,
        min_forecast_month: minimum,
        max_forecast_end: maximum,
        history_end: publishedHistoryEnd,
      });
      if (problem) {
        setBoundsError(problem);
        return;
      }
      setBoundsError("");
      setError("");
      const current = selectionRef.current;
      if (!current.startMonth || !current.endMonth || current.periodType === PERIOD_TYPE.REMAINING_YEAR) {
        if (!current.startMonth || !current.endMonth) {
          applyDefaultPeriod(minimum, maximum, publishedHistoryEnd, current.periodType, "");
          return;
        }
        const remaining = remainingMonthsFromPublishedModel({
          historyEnd: publishedHistoryEnd,
          earliestIso: minimum,
          maximumIso: maximum,
        });
        if (!remaining.available) {
          setStartMonth("");
          setEndMonth("");
          setValidation(remaining.message);
          return;
        }
        setStartMonth(remaining.startIso);
        setEndMonth(remaining.endIso);
        setValidation("");
        return;
      }
      if (current.startMonth < minimum || current.endMonth > maximum || current.startMonth > maximum) {
        setValidation(
          `The selected forecast period is outside the published model range. Choose a period from ${monthYearPlain(minimum)} through ${monthYearPlain(maximum)}.`
        );
        return;
      }
      setValidation("");
    } catch (cause) {
      setBoundsError(cause.message || "Unable to load the forecast period.");
    } finally {
      setBoundsLoading(false);
    }
  }, [applyDefaultPeriod, enabled]);

  useEffect(() => {
    refreshBounds();
  }, [refreshBounds]);

  const validate = useCallback(() => {
    if (boundsLoading) {
      return "The forecast period is still loading.";
    }
    if (boundsError) {
      return boundsError;
    }
    const periodMessage = validatePeriodTypeSelection({
      periodType,
      startIso: startMonth,
      endIso: endMonth,
      earliestIso: earliestMonth,
      historyEnd,
      maximumIso: maximumMonth,
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
    return "";
  }, [
    boundsError,
    boundsLoading,
    earliestMonth,
    endMonth,
    historyEnd,
    maximumMonth,
    periodType,
    startMonth,
  ]);

  const canSubmit = !boundsLoading && !boundsError && !validate();

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
        period_type: periodType,
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
  }, [category, endMonth, periodType, startMonth, validate]);

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
    maximumMonth,
    historyEnd,
    boundsLoading,
    boundsError,
    refreshBounds,
    forecast,
    loading,
    error,
    validation,
    setValidation,
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
