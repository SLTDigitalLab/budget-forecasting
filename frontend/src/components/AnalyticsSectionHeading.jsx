export default function AnalyticsSectionHeading({ id, title, subtitle }) {
  return (
    <header className="analytics-major-heading">
      <h2 id={id} className="analytics-section-title">
        {title}
      </h2>
      {subtitle ? <p className="analytics-section-period">{subtitle}</p> : null}
    </header>
  );
}
