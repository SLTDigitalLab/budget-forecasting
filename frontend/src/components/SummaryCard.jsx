export default function SummaryCard({ label, value }) {
  return (
    <article className="card summary-card">
      <p className="muted">{label}</p>
      <strong>{value}</strong>
    </article>
  );
}
