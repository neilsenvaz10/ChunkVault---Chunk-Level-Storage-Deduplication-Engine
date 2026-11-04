import Card from './Card'
import StatusBadge from './StatusBadge'

// Clearly labelled stand-in for a module that is not built yet.
// Shows no data: only what the module is for and where its design lives.
export default function PlaceholderPage({ title, phase, purpose, will, docs }) {
  return (
    <>
      <h1>{title}</h1>
      <Card title="Status" aside={<StatusBadge tone="warn">Not implemented</StatusBadge>}>
        <p>
          This module is a placeholder. It is planned for <strong>Phase {phase}</strong> and
          currently shows no data.
        </p>
      </Card>
      <Card title="Purpose">
        <p>{purpose}</p>
      </Card>
      {will?.length > 0 && (
        <Card title="Planned content">
          <ul>
            {will.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Card>
      )}
      {docs && (
        <Card title="Design reference">
          <code>{docs}</code>
        </Card>
      )}
    </>
  )
}
