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

export function mergeHistoricalActuals(analysisMonths, forecastActuals) {
  const byMonth = new Map();
  function add(month, amount) {
    if (!isIsoMonth(month) || !Number.isFinite(Number(amount))) {
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

export function buildForecastComparison({
  historicalActuals,
  forecast,
  historyEnd,
}) {
  const forecasts = chronologicalForecastMonths(forecast);
  const actualsByMonth = new Map(
    (Array.isArray(historicalActuals) ? historicalActuals : [])
      .filter((row) => isIsoMonth(row?.month) && Number.isFinite(Number(row.actual_amount)))
      .map((row) => [row.month, Number(row.actual_amount)])
  );
  return forecasts
    .filter((row) => isIsoMonth(row.month) && Number.isFinite(Number(row.forecast_amount)))
    .map((row) => {
      const historical_periods_used = historicalPeriodsForForecastMonth({
        forecastMonth: row.month,
        actualsByMonth,
        historyEnd,
      });
      const historical_average = averageFromPeriods(historical_periods_used);
      const forecast_amount = Number(row.forecast_amount);
      const difference = historical_average == null ? null : forecast_amount - historical_average;
      const difference_percent =
        historical_average == null || historical_average === 0
          ? null
          : (difference / historical_average) * 100;
      return {
        month: row.month,
        forecast_amount,
        historical_average,
        difference,
        difference_percent,
        observation_count: historical_periods_used.length,
        historical_periods_used,
      };
    });
}

export function comparisonTotals(rows) {
  const items = Array.isArray(rows) ? rows : [];
  const forecastTotal = items.reduce((sum, row) => sum + Number(row.forecast_amount || 0), 0);
  const baselineRows = items.filter((row) => Number.isFinite(row.historical_average));
  const historicalTotal = baselineRows.reduce((sum, row) => sum + Number(row.historical_average), 0);
  const difference = baselineRows.length ? forecastTotal - historicalTotal : null;
  const differencePercent =
    baselineRows.length && historicalTotal !== 0 ? (difference / historicalTotal) * 100 : null;
  return {
    forecast_total: items.length ? forecastTotal : null,
    historical_total: baselineRows.length ? historicalTotal : null,
    difference,
    difference_percent: differencePercent,
    month_count: items.length,
    compared_month_count: baselineRows.length,
  };
}
