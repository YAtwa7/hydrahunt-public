import { useEffect, useState } from 'react'

const PHASE_LABELS = {
  collecting: '📡 Collecting from sources',
  filtering: '🔍 Filtering',
  scoring: '🧮 Scoring against your CV',
  notifying: '📣 Sending Discord alerts',
  done: '✅ Done',
  crashed: '💥 Crashed',
  idle: 'Idle',
}

const COLLECTOR_LABELS = {
  jobspy: 'Job boards (LinkedIn/Indeed/...)',
  remote_boards: 'Remote boards',
  ats_companies: 'Company career pages',
  reddit: 'Reddit',
  hackernews: 'HN Who is Hiring',
  dorklab: 'DorkLab',
  rocketship: 'Rocketship (career-page long tail)',
}

// A scan older than this is a stale frame, not a real scan. Real scans run
// ~1-2 min; past this we keep showing the (large, honest) number but stop
// offering an ETA, rather than pretending the elapsed time is unknown.
const STALE_SCAN_S = 60 * 60

function fmt(s) {
  if (s == null) return '—'
  const v = Math.max(0, s) // never render a negative clock (skew / future started_at)
  const m = Math.floor(v / 60)
  const sec = Math.round(v % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

export default function ScanIndicator({ scan }) {
  const [elapsed, setElapsed] = useState(0)

  // tick elapsed time every second while running
  useEffect(() => {
    if (!scan?.running || !scan?.started_at) {
      setElapsed(0)
      return
    }
    const t0 = new Date(scan.started_at).getTime()
    if (!Number.isFinite(t0)) {
      setElapsed(0)
      return
    }
    const tick = () => setElapsed(Math.max(0, (Date.now() - t0) / 1000))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [scan?.running, scan?.started_at])

  if (!scan) return null
  const { running, result, error, last_scan, progress, avg_scan_s } = scan
  // ETA only while the average is a useful forecast; past that it reads 0s
  // forever and is worse than saying nothing.
  const eta =
    avg_scan_s != null && running && elapsed > 0 && elapsed <= STALE_SCAN_S
      ? Math.max(0, avg_scan_s - elapsed)
      : null
  const stale = running && elapsed > STALE_SCAN_S

  if (running) {
    const collectors = progress?.collectors || {}
    const names = Object.keys(collectors)
    const done = names.filter((n) => collectors[n].status !== 'running').length
    const phase = progress?.phase || 'collecting'
    // progress %: collectors 60%, scoring 35%, notify 5%
    let pct = names.length ? (done / names.length) * 60 : 0
    if (phase === 'filtering') pct = 62
    if (phase === 'scoring') {
      const total = progress?.filtered || 1
      pct = 65 + ((progress?.scored || 0) / total) * 30
    }
    if (phase === 'notifying') pct = 97

    return (
      <div className="scan-bar running">
        <div className="scan-top">
          <span className="spinner" />
          <span className="phase">{PHASE_LABELS[phase] || phase}</span>
          <span className="timing">
            ⏱ {fmt(elapsed)}
            {eta != null && <> · ETA ~{fmt(eta)}</>}
            {stale && <> · unusually long</>}
          </span>
          <span className="pct">{Math.round(pct)}%</span>
        </div>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="collector-chips">
          {names.map((n) => {
            const c = collectors[n]
            const icon = c.status === 'done' ? '✅' : c.status === 'failed' ? '❌' : '⏳'
            return (
              <span key={n} className={`chip coll-${c.status}`}>
                {icon} {COLLECTOR_LABELS[n] || n}
                {c.status === 'done' && ` · ${c.count}`}
              </span>
            )
          })}
          {(progress?.jobs_found ?? 0) > 0 && (
            <span className="chip found">{progress.jobs_found} found</span>
          )}
          {phase === 'scoring' && (progress?.llm_total ?? 0) > 0 && (
            <span className="chip llm">
              🤖 AI scoring {progress.llm_done}/{progress.llm_total}
            </span>
          )}
        </div>
      </div>
    )
  }
  if (error) {
    return <div className="scan-bar error">Scan crashed: {error}</div>
  }
  if (result) {
    return (
      <div className="scan-bar done">
        ✅ Scan complete: <b>{result.jobs_found}</b> scanned · <b>{result.jobs_new}</b> new ·{' '}
        <b>{result.tier_a}</b> A-tier · <b>{result.tier_b}</b> B-tier
        {result.errors?.length ? ` · ⚠️ ${result.errors.length} source errors` : ''}
      </div>
    )
  }
  if (last_scan) {
    return (
      <div className="scan-bar idle">
        Last scan: {String(last_scan.started_at).slice(0, 16).replace('T', ' ')} UTC ·{' '}
        {last_scan.jobs_found} scanned · {last_scan.jobs_new} new
        {last_scan.error ? ` · ⚠️ ${last_scan.error}` : ''}
      </div>
    )
  }
  return <div className="scan-bar idle">No scans yet — hit ▶ Run Scan.</div>
}
