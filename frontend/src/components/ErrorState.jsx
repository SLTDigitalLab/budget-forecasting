export default function ErrorState({ message }) {
  return (
    <div className="card error" role="alert">
      {message}
    </div>
  );
}
