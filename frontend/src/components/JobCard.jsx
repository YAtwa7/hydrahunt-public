import { useState } from 'react'
import { logoUrl, setStatus } from '../api'
import DraftModal from './DraftModal'

const TIER_CLASS = { A: 'tier-a', B: 'tier-b', C: 'tier-c' }
const AUTO_LABEL = {
  none: '👋 Manual',
  plus: '➕ Auto a plus',
  open_to_learn: '📚 Learn on job',
  required: '🤖 Auto required',
  unknown: '❓',
}
const LOC_LABEL = {
  ANYWHERE: '🌍 Anywhere',
  EMEA_OK: '🌐 EMEA',
  EGYPT_LISTED: '🇪🇬 Egypt listed',
  RELOCATION: '✈️ Relocation',
  HYBRID_ONSITE: '🏢 Hybrid/Onsite',
  EXCLUDED: '🚫 Excluded',
  UNKNOWN: '❓ Location',
}
const EMPLOY_LABEL = {
  full_time: '🕐 Full-time',
  part_time: '🌗 Part-time',
  contract: '📄 Contract',
  freelance: '🪂 Freelance',
}
const TZ_LABEL = {
  americas: '🌎 US hours',
  emea: '🌍 EU hours',
  apac: '🌏 APAC hours',
  flexible: '⏰ Flexible',
}

function postedAgo(posted_at, first_seen_at) {
  const raw = posted_at || first_seen_at
  if (!raw) return ''
  const d = new Date(raw)
  if (isNaN(d)) return String(raw).slice(0, 10)
  const days = Math.floor((Date.now() - d.getTime()) / 86400000)
  if (days <= 0) return '🔥 today'
  if (days === 1) return '🔥 yesterday'
  return `${days}d ago`
}

export default function JobCard({ job, onChanged }) {
  const [logoErr, setLogoErr] = useState(false)
  const [showDraft, setShowDraft] = useState(false)
  const score = job.llm_score ?? job.rule_score ?? 0
  const logo = logoUrl(job.company)

  async function mark(status) {
    await setStatus(job.id, status)
    onChanged?.()
  }

  return (
    <article className={`card ${TIER_CLASS[job.tier] || ''} status-${job.status}`}>
      <div className="card-head">
        {logo && !logoErr ? (
          <img className="logo" src={logo} alt="" onError={() => setLogoErr(true)} />
        ) : (
          <div className="logo fallback">{(job.company || '?')[0]}</div>
        )}
        <div className="title-block">
          <a className="title" href={job.source_url} target="_blank" rel="noreferrer">
            {job.title}
          </a>
          <span className="company">{job.company || '—'}</span>
        </div>
        <div className={`tier-badge ${TIER_CLASS[job.tier]}`}>{job.tier}</div>
      </div>

      <div className="meta">
        <span className="chip score">{Math.round(score)}</span>
        <span className="chip">{AUTO_LABEL[job.automation_class] || job.automation_class}</span>
        <span className="chip">{LOC_LABEL[job.location_tag] || job.location_tag}</span>
        {job.employment_type && (
          <span className="chip">{EMPLOY_LABEL[job.employment_type] || job.employment_type}</span>
        )}
        {job.timezone_fit && job.timezone_fit !== 'unknown' && (
          <span className="chip">{TZ_LABEL[job.timezone_fit] || job.timezone_fit}</span>
        )}
        {job.apply_by_email ? <span className="chip email">✉️ Email-apply</span> : null}
        {job.ghost_score >= 50 && (
          <span className="chip ghost" title={job.ghost_signals}>👻 ghost risk {job.ghost_score}</span>
        )}
        {job.times_seen > 1 && (
          <span className="chip" title="seen across multiple scans">🔁 ×{job.times_seen}</span>
        )}
        <span className="chip src">{job.source}</span>
      </div>

      {job.salary_estimate && <div className="salary">💰 {job.salary_estimate}</div>}
      {job.llm_reasoning && <p className="reasoning">{job.llm_reasoning}</p>}

      <div className="dates">
        <span>Posted: {postedAgo(job.posted_at, job.first_seen_at)}</span>
        <span>Found: {String(job.first_seen_at).slice(0, 10)}</span>
      </div>

      <div className="actions">
        <a className="btn apply" href={job.source_url} target="_blank" rel="noreferrer"
           onClick={() => mark('viewed')}>
          View ↗
        </a>
        <button className="btn" onClick={() => setShowDraft(true)}>✍️ Draft</button>
        <button className="btn" onClick={() => mark('applied')}>Applied ✓</button>
        <button className="btn ghost" onClick={() => mark('hidden')}>Hide</button>
      </div>
      {showDraft && <DraftModal job={job} onClose={() => setShowDraft(false)} />}
    </article>
  )
}
