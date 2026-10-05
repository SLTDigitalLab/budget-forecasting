import { LineChart } from "lucide-react";
import ErrorState from "../components/ErrorState";
import ForecastControls from "../components/ForecastControls";
import ForecastResults from "../components/ForecastResults";
import LoadingState from "../components/LoadingState";
import { useForecast } from "../context/ForecastContext";

export default function GenerateForecast() {
  const { forecast, loading, error } = useForecast();

  return (
    <section className="generate-page">
      <header className="page-header">
        <div>
          <h1>Generate Forecast</h1>
          <p>Select a budget category and forecast period to generate predictions.</p>
        </div>
      </header>
      <ForecastControls />
      {error ? <ErrorState message={error} /> : null}
      {forecast ? (
        <ForecastResults />
      ) : loading ? (
        <article className="card section-card generate-loading-card">
          <LoadingState label="Generating Forecast..." />
        </article>
      ) : (
        <article className="card generate-empty-state">
          <LineChart size={28} aria-hidden="true" />
          <p>Your forecast results will appear here</p>
          <span>Select a category and forecast period, then generate a forecast.</span>
        </article>
      )}
    </section>
  );
}
