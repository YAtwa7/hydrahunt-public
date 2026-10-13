import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchJobs, fetchStats, fetchScanStatus, startScan } from './api'
import JobCard from './components/JobCard'
import StatsBar from './components/StatsBar'
import ScanIndicator from './components/ScanIndicator'
import CompaniesPanel from './components/CompaniesPanel'
import DorkLabPanel from './components/DorkLabPanel'
import AskPanel from './components/AskPanel'

const TIERS = ['', 'A', 'B', 'C']
const STATUSES = ['', 'new', 'viewed', 'applied', 'interview', 'offer', 'rejected', 'hidden']
const SORTS = { date: 'Newest first', score: 'Best score' }

export default function App() {
  const [tab, setTab] = useState('jobs')
  const [jobs, setJobs] = useState([])
  const [stats, setStats] = useState(null)
  const [tier, setTier] = useState('')
  const [status, setStatus] = useState('')
  const [sort, setSort] = useState('date')
  const [scan, setScan] = useState(null)
  const [error, setError] = useState(null)
  const wasRunning = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const [j, s] = await Promise.all([fetchJobs({ tier, status, sort }), fetchStats()])
      setJobs(j)
      setStats(s)
      setError(null)
    } catch (e) {
      setError(`Failed to load jobs: ${e.message}`)
    }
  }, [tier, status, sort])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Poll scan status on a fixed cadence for the life of the page.
  // This MUST keep polling after a scan ends: the scheduler starts scans on its
  // own, and a poll that stops when it sees running:false leaves the UI frozen
  // on a stale running frame whose timer ticks forever.
  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const s = await fetchScanStatus()
        if (cancelled) return
        setScan(s)
        if (wasRunning.current && !s.running) refresh() // transition -> resync pool
        wasRunning.current = s.running
      } catch {
        /* server down — keep polling, it may come back */
      }
    }
    poll()
    const id = setInterval(poll, 4000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [refresh])

  async function handleScan() {
    setError(null)
    try {
      const res = await startScan()
      if (!res.started) {
        setError(res.reason)
        return
      }
      // the always-on poll picks this up on its next tick
      wasRunning.current = true
      setScan((prev) => ({ ...prev, running: true, started_at: res.started_at }))
    } catch (e) {
      setError(`Scan failed to start: ${e.message}`)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>🐉 HydraHunt</h1>
        <nav className="tabs">
          <button className={tab === 'jobs' ? 'tab active' : 'tab'} onClick={() => setTab('jobs')}>
            Jobs
          </button>
          <button
            className={tab === 'companies' ? 'tab active' : 'tab'}
            onClick={() => setTab('companies')}
          >
            Companies
          </button>
          <button
            className={tab === 'dorklab' ? 'tab active' : 'tab'}
            onClick={() => setTab('dorklab')}
          >
            DorkLab
          </button>
          <button
            className={tab === 'ask' ? 'tab active' : 'tab'}
            onClick={() => setTab('ask')}
          >
            Ask
          </button>
        </nav>
        <button className="scan-btn" onClick={handleScan} disabled={scan?.running}>
          {scan?.running ? '⏳ Scanning…' : '▶ Run Scan'}
        </button>
      </header>

      {error && <div className="error-banner">⚠️ {error}</div>}

      <ScanIndicator scan={scan} />

      {tab === 'jobs' ? (
        <>
          <StatsBar stats={stats} />
          <div className="filters">
            <label>
              Tier
              <select value={tier} onChange={(e) => setTier(e.target.value)}>
                {TIERS.map((t) => (
                  <option key={t} value={t}>{t || 'All'}</option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{s || 'All'}</option>
                ))}
              </select>
            </label>
            <label>
              Sort
              <select value={sort} onChange={(e) => setSort(e.target.value)}>
                {Object.entries(SORTS).map(([k, label]) => (
                  <option key={k} value={k}>{label}</option>
                ))}
              </select>
            </label>
            <span className="count">{jobs.length} jobs</span>
          </div>

          <main className="grid">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} onChanged={refresh} />
            ))}
            {jobs.length === 0 && (
              <p className="empty">No jobs match — hit ▶ Run Scan to hunt.</p>
            )}
          </main>
        </>
      ) : tab === 'companies' ? (
        <CompaniesPanel />
      ) : tab === 'ask' ? (
        <AskPanel />
      ) : (
        <DorkLabPanel />
      )}
    </div>
  )
}
