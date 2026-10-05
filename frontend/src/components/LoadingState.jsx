export default function LoadingState({ label = "Generating forecast..." }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <div>
        <div className="skeleton" />
        <p>{label}</p>
      </div>
    </div>
  );
}
