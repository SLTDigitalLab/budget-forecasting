function chronologicalForecastMonths(forecast) {
  return (Array.isArray(forecast?.monthly_forecasts) ? forecast.monthly_forecasts.slice() : [])
    .filter((row) => row && row.month)
    .sort((left, right) => String(left.month).localeCompare(String(right.month)));
}

function isIsoMonth(value) {
  return /^\d{4}-\d{2}$/.test(String(value || ""));
}

function calendarMonth(isoMonth) {
  return String(isoMonth).slice(5, 7);
}

/** Finite amount check that treats null/empty as missing (Number(null) === 0). */
export function isFiniteAmount(value) {
  if (value == null || value === "") {
    return false;
  }
  const number = Number(value);
  return Number.isFinite(number);
}

/**
 * Budget Codes that contribute finite forecast amounts (zeros and negatives kept).
 * Derived from account_monthly_forecasts only — not selected_account_count or category membership.
 */
export function contributingForecastCodes(forecast) {
  const codes = new Set();
  (Array.isArray(forecast?.account_monthly_forecasts) ? forecast.account_monthly_forecasts : []).forEach((row) => {
    const code = String(row?.budget_code || "").trim();
    if (!code || !isFiniteAmount(row?.forecast_amount)) {
      return;
    }
    codes.add(code);
  });
  return Array.from(codes).sort((left, right) => left.localeCompare(right));
}

/**
 * Per-month finite forecast amounts by contributing code.
 * Unavailable / non-finite codes are omitted entirely.
 */
export function accountForecastsByMonth(forecast) {
  const byMonth = new Map();
  (Array.isArray(forecast?.account_monthly_forecasts) ? forecast.account_monthly_forecasts : []).forEach((row) => {
    const month = String(row?.month || "");
    const code = String(row?.budget_code || "").trim();
    if (!isIsoMonth(month) || !code || !isFiniteAmount(row?.forecast_amount)) {
      return;
    }
    if (!byMonth.has(month)) {
      byMonth.set(month, new Map());
    }
    byMonth.get(month).set(code, Number(row.forecast_amount));
  });
  return byMonth;
}

/**
 * Confirmed code→month→amount map. Missing/null amounts are omitted (never coerced to 0).
 */
export function codeActualsByCode(codeActuals) {
  const byCode = new Map();
  (Array.isArray(codeActuals) ? codeActuals : []).forEach((row) => {
    const code = String(row?.budget_code || "").trim();
    const month = String(row?.month || "");
    if (!code || !isIsoMonth(month) || !isFiniteAmount(row?.actual_amount)) {
      return;
    }
    if (!byCode.has(code)) {
      byCode.set(code, new Map());
    }
    byCode.get(code).set(month, Number(row.actual_amount));
  });
  return byCode;
}

export function mergeHistoricalActuals(analysisMonths, forecastActuals) {
  const byMonth = new Map();
  function add(month, amount) {
    if (!isIsoMonth(month) || !isFiniteAmount(amount)) {
      return;
    }
    if (!byMonth.has(month)) {
      byMonth.set(month, Number(amount));
    }
  }
  (Array.isArray(analysisMonths) ? analysisMonths : []).forEach((row) => {
    add(row?.month, row?.actual_amount);
  });
  (Array.isArray(forecastActuals) ? forecastActuals : []).forEach((row) => {
    add(row?.month, row?.actual_amount);
  });
  return Array.from(byMonth.entries())
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([month, actual_amount]) => ({ month, actual_amount }));
}

export function historicalPeriodsForForecastMonth({
  forecastMonth,
  actualsByMonth,
  historyEnd,
  historyStart = null,
}) {
  if (!isIsoMonth(forecastMonth)) {
    return [];
  }
  const target = calendarMonth(forecastMonth);
  return Array.from(actualsByMonth.entries())
    .filter(([month, actual]) => {
      if (!isIsoMonth(month) || calendarMonth(month) !== target) {
        return false;
      }
      if (month >= forecastMonth) {
        return false;
      }
      if (historyStart && month < historyStart) {
        return false;
      }
      if (historyEnd && month > historyEnd) {
        return false;
      }
      return Number.isFinite(actual);
    })
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([month, actual]) => ({ month, actual }));
}

export function averageFromPeriods(periods) {
  const rows = Array.isArray(periods) ? periods : [];
  if (!rows.length) {
    return null;
  }
  const total = rows.reduce((sum, row) => sum + Number(row.actual), 0);
  if (!Number.isFinite(total)) {
    return null;
  }
  return total / rows.length;
}

function buildAggregateForecastComparison({
  historicalActuals,
  forecast,
  historyEnd,
  historyStart = null,
}) {
  const forecasts = chronologicalForecastMonths(forecast);
  const actualsByMonth = new Map(
    (Array.isArray(historicalActuals) ? historicalActuals : [])
      .filter((row) => isIsoMonth(row?.month) && isFiniteAmount(row.actual_amount))
      .map((row) => [row.month, Number(row.actual_amount)])
  );
  return forecasts
    .filter((row) => isIsoMonth(row.month) && isFiniteAmount(row.forecast_amount))
    .map((row) => {
      const historical_periods_used = historicalPeriodsForForecastMonth({
        forecastMonth: row.month,
        actualsByMonth,
        historyEnd,
        historyStart,
      });
      const historical_average = averageFromPeriods(historical_periods_used);
      const forecast_amount = Number(row.forecast_amount);
      const matched_forecast_amount = historical_average == null ? null : forecast_amount;
      const difference = historical_average == null ? null : forecast_amount - historical_average;
      const difference_percent =
        historical_average == null || historical_average === 0
          ? null
          : (difference / historical_average) * 100;
      return {
        month: row.month,
        forecast_amount,
        matched_forecast_amount,
        historical_average,
        difference,
        difference_percent,
        observation_count: historical_periods_used.length,
        matched_code_count: historical_average == null ? 0 : null,
        historical_periods_used,
        comparison_scope: "aggregate",
      };
    });
}

/**
 * Code/month matched same-calendar-month comparison.
 * Only pairs with BOTH a finite forecast amount and a valid historical baseline enter matched sums.
 */
export function buildCodeMatchedForecastComparison({
  codeActuals,
  forecast,
  historyEnd,
  historyStart = null,
}) {
  const forecasts = chronologicalForecastMonths(forecast);
  const byCode = codeActualsByCode(codeActuals);
  const forecastsByMonth = accountForecastsByMonth(forecast);

  return forecasts
    .filter((row) => isIsoMonth(row.month) && isFiniteAmount(row.forecast_amount))
    .map((row) => {
      const month = row.month;
      const codeForecasts = forecastsByMonth.get(month) || new Map();
      let fullForecast = 0;
      let hasFull = false;
      codeForecasts.forEach((amount) => {
        fullForecast += amount;
        hasFull = true;
      });
      // Prefer the published monthly total when present so the full-forecast KPI stays aligned.
      const forecast_amount = isFiniteAmount(row.forecast_amount)
        ? Number(row.forecast_amount)
        : hasFull
          ? fullForecast
          : null;

      let matchedForecast = 0;
      let matchedHistorical = 0;
      let matchedCodeCount = 0;
      const periodSums = new Map();

      codeForecasts.forEach((forecastAmount, code) => {
        const actualsByMonth = byCode.get(code) || new Map();
        const periods = historicalPeriodsForForecastMonth({
          forecastMonth: month,
          actualsByMonth,
          historyEnd,
          historyStart,
        });
        const baseline = averageFromPeriods(periods);
        if (baseline == null) {
          // No historical baseline: drop this code from BOTH matched sides.
          return;
        }
        matchedForecast += forecastAmount;
        matchedHistorical += baseline;
        matchedCodeCount += 1;
        periods.forEach((period) => {
          const current = periodSums.get(period.month) || { actual: 0, code_count: 0 };
          current.actual += Number(period.actual);
          current.code_count += 1;
          periodSums.set(period.month, current);
        });
      });

      const historical_periods_used = Array.from(periodSums.entries())
        .sort((left, right) => left[0].localeCompare(right[0]))
        .map(([periodMonth, info]) => ({
          month: periodMonth,
          actual: info.actual,
          code_count: info.code_count,
        }));

      const hasMatch = matchedCodeCount > 0;
      const matched_forecast_amount = hasMatch ? matchedForecast : null;
      const historical_average = hasMatch ? matchedHistorical : null;
      const difference = hasMatch ? matchedForecast - matchedHistorical : null;
      const difference_percent =
        !hasMatch || matchedHistorical === 0 ? null : (difference / matchedHistorical) * 100;

      return {
        month,
        forecast_amount,
        matched_forecast_amount,
        historical_average,
        difference,
        difference_percent,
        observation_count: historical_periods_used.length,
        matched_code_count: matchedCodeCount,
        historical_periods_used,
        comparison_scope: "code_matched",
      };
    });
}

export function buildForecastComparison({
  historicalActuals,
  codeActuals,
  forecast,
  historyEnd,
  historyStart = null,
}) {
  if (Array.isArray(codeActuals)) {
    return buildCodeMatchedForecastComparison({
      codeActuals,
      forecast,
      historyEnd,
      historyStart,
    });
  }
  return buildAggregateForecastComparison({
    historicalActuals,
    forecast,
    historyEnd,
    historyStart,
  });
}

function matchedForecastAmountOf(row) {
  if (isFiniteAmount(row?.matched_forecast_amount)) {
    return Number(row.matched_forecast_amount);
  }
  // Legacy aggregate rows: when a baseline exists, the full month forecast was the matched amount.
  if (Number.isFinite(row?.historical_average) && isFiniteAmount(row?.forecast_amount)) {
    return Number(row.forecast_amount);
  }
  return null;
}

export function comparisonTotals(rows) {
  const items = Array.isArray(rows) ? rows : [];
  // Preserve the full forecast-total KPI across every forecast month.
  const forecastTotal = items.reduce((sum, row) => {
    const amount = Number(row.forecast_amount);
    return Number.isFinite(amount) ? sum + amount : sum;
  }, 0);
  // Difference / relative change use only matched code/month pairs (via per-month matched sums).
  const matchedRows = items
    .map((row) => ({ row, matchedForecast: matchedForecastAmountOf(row) }))
    .filter(
      ({ row, matchedForecast }) => matchedForecast != null && Number.isFinite(row.historical_average)
    );
  const matchedForecastTotal = matchedRows.reduce((sum, item) => sum + item.matchedForecast, 0);
  const historicalTotal = matchedRows.reduce((sum, item) => sum + Number(item.row.historical_average), 0);
  const difference = matchedRows.length ? matchedForecastTotal - historicalTotal : null;
  const differencePercent =
    matchedRows.length && historicalTotal !== 0 ? (difference / historicalTotal) * 100 : null;
  return {
    forecast_total: items.some((row) => isFiniteAmount(row.forecast_amount)) ? forecastTotal : null,
    historical_total: matchedRows.length ? historicalTotal : null,
    matched_forecast_total: matchedRows.length ? matchedForecastTotal : null,
    difference,
    difference_percent: differencePercent,
    month_count: items.length,
    compared_month_count: matchedRows.length,
  };
}
