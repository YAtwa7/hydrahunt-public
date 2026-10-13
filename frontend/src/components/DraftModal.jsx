import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

export default function DraftModal({ job, onClose }) {
  const [draft, setDraft] = useState(null)
  const [answers, setAnswers] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState('')

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const r = await fetch(`/api/jobs/${job.id}/draft`, { method: 'POST' })
        const data = await r.json()
        if (!alive) return
        if (data.error) setError(data.error)
        else setDraft(data)
      } catch (e) {
        if (alive) setError(e.message)
      } finally {
        if (alive) setLoading(false)
      }
    })()
    // reusable, evidence-bounded answers (works even if the draft fails)
    fetch('/api/answers')
      .then((r) => r.json())
      .then((d) => {
        if (alive && Array.isArray(d)) setAnswers(d)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [job.id])

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  function copy(text, key) {
    if (!navigator.clipboard?.writeText) return
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key)
      setTimeout(() => setCopied(''), 1500)
    }).catch(() => {})
  }

  function copyAll() {
    if (!draft) return
    const parts = [
      `SUMMARY\n${draft.summary}`,
      `WHY THIS ROLE\n${draft.why_this_role}`,
      `WHY YOU\n${draft.why_you}`,
      ...(draft.experience_answers || []).map((a) => `Q: ${a.question}\nA: ${a.answer}`),
      `COVER EMAIL\n${draft.cover_email}`,
      `GAPS\n${(draft.gaps || []).join('\n')}`,
    ]
    copy(parts.join('\n\n---\n\n'), 'all')
  }

  // Rendered through a portal: JobCard's :hover transform would otherwise turn
  // this fixed-position backdrop into a containing block and make it flicker.
  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>✍️ Draft — {job.title}</h3>
          <button className="btn ghost" onClick={onClose}>✕</button>
        </div>
        {loading && <p className="muted">🧠 Generating with AI… (~30-60s, evidence-bounded)</p>}
        {error && <p className="error-banner">⚠️ {error}</p>}
        {draft && (
          <div className="draft-body">
            <Section title="Summary" text={draft.summary} onCopy={copy} copied={copied} k="summary" />
            <Section title="Why this role" text={draft.why_this_role} onCopy={copy} copied={copied} k="role" />
            <Section title="Why you" text={draft.why_you} onCopy={copy} copied={copied} k="you" />
            {(draft.experience_answers || []).map((a, i) => (
              <Section key={i} title={`Q: ${a.question}`} text={a.answer} onCopy={copy} copied={copied} k={`qa${i}`} />
            ))}
            {draft.cover_email && (
              <Section title="📧 Cover email" text={draft.cover_email} onCopy={copy} copied={copied} k="email" />
            )}
            {(draft.gaps || []).length > 0 && (
              <Section title="⚠️ Honest gaps" text={draft.gaps.join('\n')} onCopy={copy} copied={copied} k="gaps" />
            )}
            <button className="btn apply" onClick={copyAll}>
              {copied === 'all' ? '✓ Copied!' : '📋 Copy all'}
            </button>
            <p className="muted small">model: {draft._model} · review before sending — never auto-submitted</p>
          </div>
        )}
        {answers.length > 0 && (
          <details className="answers-bank" open={!draft && !loading}>
            <summary>♻️ Reusable answers (evidence-bounded)</summary>
            <div className="draft-body">
              {answers.map((a, i) => (
                <Section
                  key={i}
                  title={`Q: ${a.question}`}
                  text={a.answer}
                  onCopy={copy}
                  copied={copied}
                  k={`bank${i}`}
                />
              ))}
            </div>
          </details>
        )}
      </div>
    </div>,
    document.body,
  )
}

function Section({ title, text, onCopy, copied, k }) {
  return (
    <div className="draft-section">
      <div className="draft-section-head">
        <strong>{title}</strong>
        <button className="btn ghost small" onClick={() => onCopy(text, k)}>
          {copied === k ? '✓' : '📋'}
        </button>
      </div>
      <p>{text}</p>
    </div>
  )
}
