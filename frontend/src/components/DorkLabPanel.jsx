import { useCallback, useEffect, useState } from 'react'

const FAMILY_ICONS = {
  ats: '🏢',
  email_apply: '✉️',
  linkedin_posts: '💼',
  x_posts: '𝕏',
  notion: '📝',
  funding: '💰',
  niche: '🧬',
  misc: '🔍',
}

export default function DorkLabPanel() {
  const [dorks, setDorks] = useState([])
  const [evolving, setEvolving] = useState(false)
  const [msg, setMsg] = useState(null)

  const refresh = useCallback(async () => {
    const r = await fetch('/api/dorks')
    setDorks(await r.json())
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function evolve() {
    setEvolving(true)
    setMsg(null)
    try {
      const r = await fetch('/api/dorks/evolve', { method: 'POST' })
      const res = await r.json()
      setMsg(`Evolution: bred ${res.bred} new dorks, retired ${res.retired}`)
      refresh()
    } finally {
      setEvolving(false)
    }
  }

  const active = dorks.filter((d) => !d.retired)
  const retired = dorks.filter((d) => d.retired)
  const maxGen = Math.max(0, ...dorks.map((d) => d.generation))

  return (
    <div className="dorklab">
      <div className="dork-header">
        <p className="muted">
          The engine breeds Google dorks, measures their yield, and evolves winners weekly.
          Generation {maxGen} · {active.length} active · {retired.length} retired
        </p>
        <button className="btn apply" onClick={evolve} disabled={evolving}>
          {evolving ? '🧬 Evolving…' : '🧬 Run Evolution Now'}
        </button>
      </div>
      {msg && <div className="scan-bar done">{msg}</div>}

      <div className="dork-list">
        {active.map((d) => (
          <DorkRow key={d.id} dork={d} />
        ))}
        {active.length === 0 && (
          <p className="empty">No dorks yet — they seed on the next scan.</p>
        )}
      </div>

      {retired.length > 0 && (
        <details className="retired-block">
          <summary>🪦 Retired dorks ({retired.length})</summary>
          <div className="dork-list">
            {retired.map((d) => (
              <DorkRow key={d.id} dork={d} />
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

function DorkRow({ dork }) {
  const pct = Math.round(dork.fitness * 100)
  const legendary = dork.fitness >= 0.6 && dork.runs >= 3
  return (
    <div className={`dork-row ${dork.retired ? 'retired' : ''}`}>
      <span className="dork-icon">{FAMILY_ICONS[dork.family] || '🔍'}</span>
      <div className="dork-body">
        <code className="dork-query">{dork.query}</code>
        <div className="dork-meta">
          <span>gen {dork.generation}</span>
          <span>{dork.runs} runs</span>
          <span>{dork.valid_results}/{dork.total_results} valid</span>
          {legendary && <span className="legendary">🏆 LEGENDARY</span>}
        </div>
      </div>
      <div className="dork-fitness">
        <div className="fitness-track">
          <div
            className={`fitness-fill ${pct >= 60 ? 'high' : pct >= 25 ? 'mid' : 'low'}`}
            style={{ width: `${Math.max(pct, 2)}%` }}
          />
        </div>
        <span className="fitness-num">{pct}%</span>
      </div>
    </div>
  )
}
