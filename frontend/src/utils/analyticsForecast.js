export async function resolveAnalyticsForecast({ currentForecast, fetchLatest }) {
  if (currentForecast) {
    return { status: "memory", forecast: currentForecast };
  }
  const latest = await fetchLatest();
  if (!latest) {
    return { status: "empty", forecast: null };
  }
  return { status: "history", forecast: latest };
}
