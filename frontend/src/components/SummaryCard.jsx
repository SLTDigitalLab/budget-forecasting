export default function SummaryCard({ label, value, detail, className = "" }) {
  const classes = ["card", "summary-card", className].filter(Boolean).join(" ");
  return (
    <article className={classes}>
      <p className="muted">{label}</p>
      <strong>{value}</strong>
      {detail ? <span className="summary-card-detail">{detail}</span> : null}
    </article>
  );
}
