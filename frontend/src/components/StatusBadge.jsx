// tone: ok | warn | error | muted
export default function StatusBadge({ tone = 'muted', children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}
