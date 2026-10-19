export default function PlaceholderPage({ title }) {
  return (
    <section>
      <header className="page-header">
        <div>
          <h1>{title}</h1>
          <p>This area will be available in a later release.</p>
        </div>
      </header>
      <article className="card coming-soon">
        <p>Coming soon</p>
      </article>
    </section>
  );
}
