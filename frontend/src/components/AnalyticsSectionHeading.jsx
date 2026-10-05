export default function AnalyticsSectionHeading({ id, index, title }) {
  return (
    <header className="analytics-major-heading">
      <span className="analytics-section-kicker">{index}</span>
      <h2 id={id} className="analytics-section-title">
        {title}
      </h2>
    </header>
  );
}
