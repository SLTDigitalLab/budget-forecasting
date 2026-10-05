function isFiniteAmount(value) {
  return Number.isFinite(Number(value));
}

export function forecastDistributionByCode(forecast) {
  const rows = Array.isArray(forecast?.account_monthly_forecasts)
    ? forecast.account_monthly_forecasts
    : [];
  const totals = new Map();
  rows.forEach((row) => {
    const code = String(row.budget_code || "");
    if (!code || !isFiniteAmount(row.forecast_amount)) {
      return;
    }
    const current = totals.get(code) || {
      budget_code: code,
      account_name: "",
      forecast_amount: 0,
    };
    current.forecast_amount += Number(row.forecast_amount);
    if (row.account_name) {
      current.account_name = row.account_name;
    }
    totals.set(code, current);
  });
  const items = Array.from(totals.values()).sort((left, right) => {
    const delta = right.forecast_amount - left.forecast_amount;
    return delta !== 0 ? delta : String(left.budget_code).localeCompare(String(right.budget_code));
  });
  const total = items.reduce((sum, row) => sum + row.forecast_amount, 0);
  return items.map((row) => ({
    ...row,
    percent: total === 0 ? 0 : row.forecast_amount / total,
    total,
  }));
}
