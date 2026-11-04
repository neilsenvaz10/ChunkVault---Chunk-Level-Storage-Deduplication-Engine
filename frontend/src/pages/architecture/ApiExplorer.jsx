import { useMemo, useState } from 'react'
import { AsyncState, Card, StatusBadge } from '../../components/ui'
import { useApi } from '../../hooks/useApi'
import { api } from '../../services/api'

// Project annotations that OpenAPI cannot express. Everything else (routes, parameters,
// request/response fields, status codes) is derived from FastAPI's generated /api/openapi.json.
export const ANNOTATIONS = {
  'POST /api/files': { component: 'Upload orchestrator', effect: 'mutates' },
  'GET /api/files': { component: 'SQLite repository', effect: 'reads' },
  'GET /api/files/{file_id}': { component: 'SQLite repository', effect: 'reads' },
  'GET /api/files/{file_id}/download': { component: 'Restoration engine', effect: 'reads (records a restore operation row)' },
  'DELETE /api/files/{file_id}': { component: 'File deleter + SQLite', effect: 'mutates' },
  'GET /api/chunks': { component: 'SQLite repository', effect: 'reads' },
  'GET /api/chunks/{chunk_hash}': { component: 'SQLite repository', effect: 'reads' },
  'GET /api/storage/stats': { component: 'Storage statistics', effect: 'reads' },
  'POST /api/integrity/audit': { component: 'Integrity auditor', effect: 'reads (POST because it runs a long computation; changes nothing)' },
  'POST /api/integrity/reconcile': { component: 'Reconciler', effect: 'mutates (explicit cleanup)' },
  'GET /api/inspect/schema': { component: 'Database inspection', effect: 'reads' },
  'GET /api/inspect/tables/{table}/rows': { component: 'Database inspection', effect: 'reads' },
  'GET /api/health': { component: 'FastAPI request layer', effect: 'reads' },
  'GET /api/system/config': { component: 'Configuration', effect: 'reads' },
  'GET /api/system/chunking-demo': { component: 'CDC engine (generated data)', effect: 'reads (computes; stores nothing)' },
  'GET /api/planned-endpoints': { component: 'Endpoint registry', effect: 'reads' },
}

const METHODS = ['get', 'post', 'put', 'patch', 'delete']

// Resolve $ref chains into a flat list of {name, type, required, description}.
export function schemaFields(spec, schema) {
  if (!schema) return []
  let s = schema
  const seen = new Set()
  while (s.$ref && !seen.has(s.$ref)) {
    seen.add(s.$ref)
    s = spec.components?.schemas?.[s.$ref.split('/').pop()] || {}
  }
  if (s.type === 'array') return [{ name: '(array)', type: `${typeName(spec, s.items)}[]`, required: true, description: '' }]
  const required = new Set(s.required || [])
  return Object.entries(s.properties || {}).map(([name, p]) => ({
    name, type: typeName(spec, p), required: required.has(name), description: p.description || '',
  }))
}

export function typeName(spec, p) {
  if (!p) return 'any'
  if (p.$ref) return p.$ref.split('/').pop()
  if (p.anyOf) return p.anyOf.map((x) => typeName(spec, x)).join(' | ')
  if (p.type === 'array') return `${typeName(spec, p.items)}[]`
  return p.enum ? `enum(${p.enum.join('|')})` : (p.type || 'any')
}

const refName = (s) => s?.$ref?.split('/').pop() || s?.title || s?.type

export function listEndpoints(spec) {
  const out = []
  Object.entries(spec.paths || {}).forEach(([path, item]) => {
    if (!path.startsWith('/api/')) return
    METHODS.forEach((m) => {
      const op = item[m]
      if (!op) return
      const key = `${m.toUpperCase()} ${path}`
      const body = op.requestBody?.content
      const bodyType = body && Object.keys(body)[0]
      out.push({
        key, method: m.toUpperCase(), path, summary: op.summary || '', note: ANNOTATIONS[key],
        parameters: (op.parameters || []).map((p) => ({ name: p.name, in: p.in, required: !!p.required, type: typeName(spec, p.schema) })),
        request: bodyType ? { contentType: bodyType, schema: refName(body[bodyType].schema), fields: schemaFields(spec, body[bodyType].schema) } : null,
        responses: Object.entries(op.responses || {}).map(([status, r]) => {
          const content = r.content && Object.values(r.content)[0]
          return { status, description: r.description, schema: content ? refName(content.schema) : null, fields: content ? schemaFields(spec, content.schema) : [] }
        }),
      })
    })
  })
  return out
}

function FieldTable({ fields }) {
  if (!fields.length) return <span className="muted">no named fields</span>
  return (
    <table>
      <thead><tr><th>Field</th><th>Type</th><th>Required</th></tr></thead>
      <tbody>{fields.map((f) => <tr key={f.name} title={f.description}><td><code>{f.name}</code></td><td>{f.type}</td><td>{f.required ? 'yes' : 'no'}</td></tr>)}</tbody>
    </table>
  )
}

export default function ApiExplorer() {
  const spec = useApi((signal) => api.openapi(signal))
  const [selected, setSelected] = useState(null)
  const endpoints = useMemo(() => (spec.data ? listEndpoints(spec.data) : []), [spec.data])
  const ep = endpoints.find((e) => e.key === selected) || null

  return (
    <Card title="API communication explorer" aside={<StatusBadge tone="ok">derived from FastAPI's OpenAPI document</StatusBadge>}>
      <p className="muted">
        Routes, parameters, fields and status codes below are read live from the running server&rsquo;s <code>/api/openapi.json</code>.
        Only the &ldquo;backend component&rdquo; and &ldquo;reads / mutates&rdquo; columns are project annotations.{' '}
        <a href="/api/docs" target="_blank" rel="noreferrer">Open the interactive FastAPI docs →</a>
      </p>
      <AsyncState state={spec} onRetry={() => spec.reload()} isEmpty={() => endpoints.length === 0} empty="The server exposes no /api endpoints.">
        {() => (
          <div className="two-col api-grid">
            <div className="table-wrap">
              <table aria-label="Endpoints">
                <thead><tr><th>Method</th><th>Route</th><th>Effect</th></tr></thead>
                <tbody>
                  {endpoints.map((e) => (
                    <tr key={e.key} className={selected === e.key ? 'row-selected' : ''}>
                      <td><code>{e.method}</code></td>
                      <td>
                        <button className="link-btn" onClick={() => setSelected(e.key)} aria-pressed={selected === e.key}>{e.path}</button>
                      </td>
                      <td>{e.note ? (e.note.effect.startsWith('mutates') ? 'mutates' : 'reads') : <span className="muted">not annotated</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="details" aria-live="polite">
              {!ep ? <p className="empty">Select an endpoint to see its contract.</p> : (
                <>
                  <h3>{ep.method} {ep.path}</h3>
                  <p>{ep.summary}</p>
                  <dl className="defs">
                    <dt>Backend component</dt><dd>{ep.note?.component || <span className="muted">not annotated</span>}</dd>
                    <dt>Reads or mutates data</dt><dd>{ep.note?.effect || <span className="muted">not annotated</span>}</dd>
                    <dt>Parameters</dt>
                    <dd>{ep.parameters.length ? ep.parameters.map((p) => <div key={p.name}><code>{p.name}</code> ({p.in}, {p.type}{p.required ? ', required' : ''})</div>) : <span className="muted">none</span>}</dd>
                    <dt>Request body</dt>
                    <dd>{ep.request ? <><code>{ep.request.contentType}</code>{ep.request.schema && <> · <code>{ep.request.schema}</code></>}<FieldTable fields={ep.request.fields} /></> : <span className="muted">none</span>}</dd>
                    <dt>Responses</dt>
                    <dd>
                      {ep.responses.map((r) => (
                        <div key={r.status} className="resp">
                          <strong>{r.status}</strong> {r.description}{r.schema && <> · <code>{r.schema}</code></>}
                          {r.fields.length > 0 && <FieldTable fields={r.fields} />}
                        </div>
                      ))}
                    </dd>
                  </dl>
                </>
              )}
            </div>
          </div>
        )}
      </AsyncState>
    </Card>
  )
}
