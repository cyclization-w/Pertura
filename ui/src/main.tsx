import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { filterResults } from './dashboardModel'

type Result = {
  result_id: string
  result_kind: string
  capability_id: string
  capability_trust?: string
  status: string
  summary: string
  blockers: string[]
  cautions: string[]
  receipt_id?: string
  stale: boolean
  output_paths: string[]
}

type RunProjection = {
  run_id: string
  contract: any | null
  results: Result[]
  phases: { phase: number; title: string; status: string; result_ids: string[] }[]
  target_failure_queue: Result[]
  report: string | null
  permissions: { can_run: boolean; can_confirm_design: boolean }
}

const statusLabel: Record<string, string> = {
  completed: 'Complete',
  screen_passed: 'Passed',
  caution: 'Caution',
  completed_with_caution: 'Caution',
  blocked: 'Blocked',
  pending: 'Pending',
}

function App() {
  const [data, setData] = useState<RunProjection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('all')
  const [confirmField, setConfirmField] = useState('control')
  const [confirmValue, setConfirmValue] = useState('')
  const [confirmRationale, setConfirmRationale] = useState('')

  const refresh = () => fetch('/api/run').then(r => {
    if (!r.ok) throw new Error(`Dashboard API returned ${r.status}`)
    return r.json()
  }).then(setData).catch(e => setError(String(e)))

  useEffect(() => {
    refresh()
    const events = new EventSource('/api/events')
    events.onmessage = refresh
    events.addEventListener('result_committed', refresh)
    events.addEventListener('design_confirmed', refresh)
    return () => events.close()
  }, [])

  const visibleResults = useMemo(() => filterResults(data?.results ?? [], filter), [data, filter])

  async function submitConfirmation(event: React.FormEvent) {
    event.preventDefault()
    if (!data?.contract) return
    const response = await fetch(`/runs/${data.run_id}/confirmations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contract_id: data.contract.contract_id, field: confirmField, value: confirmValue, rationale: confirmRationale }),
    })
    if (!response.ok) {
      setError((await response.json()).detail ?? 'Confirmation failed')
      return
    }
    setConfirmValue('')
    setConfirmRationale('')
    refresh()
  }

  if (error) return <main className="fatal"><p className="eyebrow">Pertura runtime</p><h1>Dashboard unavailable</h1><p>{error}</p></main>
  if (!data) return <main className="fatal"><p className="eyebrow">Pertura runtime</p><h1>Loading verified run…</h1></main>

  const unresolved: string[] = data.contract?.unresolved_fields ?? []
  return (
    <div className="shell">
      <aside>
        <div className="brand"><span className="mark">P</span><div><strong>Pertura</strong><small>Verified Perturb-seq</small></div></div>
        <div className="run-ref"><span>RUN</span><code>{data.run_id}</code></div>
        <nav aria-label="Analysis phases">
          {data.phases.map(phase => <a key={phase.phase} href={`#phase-${phase.phase}`} className={`phase ${phase.status}`}>
            <span className="phase-num">{String(phase.phase).padStart(2, '0')}</span>
            <span>{phase.title}</span>
            <i aria-label={phase.status}></i>
          </a>)}
        </nav>
        <div className="readonly"><span>Read-only analysis surface</span><small>Only design identity can be confirmed here.</small></div>
      </aside>

      <main>
        <header><div><p className="eyebrow">Scientific run overview</p><h1>Evidence, results and receipts</h1></div><div className="header-meta"><span>DOMAIN TOOLS</span><strong>5</strong><small>CodeAct remains available</small></div></header>

        <section className="metrics">
          <article><span>Committed results</span><strong>{data.results.length}</strong><small>{data.results.filter(r => !r.stale).length} current</small></article>
          <article><span>Unresolved fields</span><strong>{unresolved.length}</strong><small>{unresolved.slice(0, 2).join(', ') || 'None'}</small></article>
          <article><span>Target failures</span><strong>{data.target_failure_queue.length}</strong><small>Require review</small></article>
          <article><span>Run controls</span><strong className="locked">Locked</strong><small>No run/retry/cancel</small></article>
        </section>

        <section className="grid">
          <article className="panel contract-panel">
            <div className="panel-title"><div><p className="eyebrow">Dataset contract</p><h2>Design identity</h2></div><code>{data.contract?.contract_id ?? 'not inspected'}</code></div>
            {data.contract ? <>
              <dl><div><dt>Format</dt><dd>{data.contract.input_format}</dd></div><div><dt>Dataset</dt><dd>{data.contract.dataset_id}</dd></div><div><dt>Version</dt><dd>v{data.contract.contract_version}</dd></div></dl>
              <div className="tags">{unresolved.map(field => <span key={field}>{field}</span>)}{!unresolved.length && <span className="good">All required identity resolved</span>}</div>
            </> : <p>Run <code>pertura inspect</code> to create a contract.</p>}
          </article>

          <article className="panel confirmation-panel">
            <div className="panel-title"><div><p className="eyebrow">Design confirmation</p><h2>Resolve identity</h2></div><span className="write-badge">ONLY WRITE API</span></div>
            <form onSubmit={submitConfirmation}>
              <label>Field<select value={confirmField} onChange={e => setConfirmField(e.target.value)}>{['control','guide_target','replicate','state_label','donor','batch'].map(item => <option key={item}>{item}</option>)}</select></label>
              <label>Confirmed value<input value={confirmValue} onChange={e => setConfirmValue(e.target.value)} required /></label>
              <label>Rationale<input value={confirmRationale} onChange={e => setConfirmRationale(e.target.value)} required /></label>
              <button type="submit" disabled={!data.contract}>Create new contract version</button>
            </form>
          </article>
        </section>

        <section className="panel results-panel">
          <div className="panel-title"><div><p className="eyebrow">Commit store projection</p><h2>Capability results</h2></div><select aria-label="Filter status" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All verdicts</option><option value="blocked">Blocked</option><option value="caution">Caution</option><option value="completed">Completed</option></select></div>
          <div className="result-list">
            {visibleResults.map(result => <article key={result.result_id} className={result.stale ? 'stale' : ''}>
              <div><span className={`status ${result.status}`}>{statusLabel[result.status] ?? result.status}</span><code>{result.capability_id}</code>{result.stale && <span className="status stale-label">STALE</span>}</div>
              <p>{result.summary}</p>
              <footer><code>{result.result_id}</code><span>→</span><code>{result.receipt_id ?? 'no receipt'}</code></footer>
              {(result.blockers.length > 0 || result.cautions.length > 0) && <ul>{[...result.blockers, ...result.cautions].map((item, i) => <li key={i}>{item}</li>)}</ul>}
            </article>)}
            {!visibleResults.length && <div className="empty">No results match this view.</div>}
          </div>
        </section>

        <section className="grid lower">
          <article className="panel"><div className="panel-title"><div><p className="eyebrow">Target reliability</p><h2>Failure queue</h2></div><strong>{data.target_failure_queue.length}</strong></div>{data.target_failure_queue.length ? data.target_failure_queue.map(item => <p key={item.result_id}>{item.summary}</p>) : <p className="muted">No target reliability failures committed.</p>}</article>
          <article className="panel"><div className="panel-title"><div><p className="eyebrow">Final surface</p><h2>Report</h2></div></div><pre>{data.report ?? 'Finalize the run to render the signed report.'}</pre></article>
        </section>
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
