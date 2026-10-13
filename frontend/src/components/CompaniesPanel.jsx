import { useCallback, useEffect, useState } from 'react'
import { addCompany, fetchCompanies, updateCompany } from '../api'

export default function CompaniesPanel() {
  const [companies, setCompanies] = useState([])
  const [newName, setNewName] = useState('')
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      setCompanies(await fetchCompanies())
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function handleAdd(e) {
    e.preventDefault()
    if (!newName.trim()) return
    try {
      const res = await addCompany(newName.trim())
      setNewName('')
      if (!res.ats) setError(`Added ${res.name} — no ATS detected, will monitor as watchlist`)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  async function toggle(c, field, value) {
    await updateCompany(c.id, { [field]: value })
    refresh()
  }

  const active = companies.filter((c) => c.enabled && !c.blacklisted)
  const watchlist = companies.filter((c) => !c.enabled && !c.blacklisted)
  const blacklisted = companies.filter((c) => c.blacklisted)

  return (
    <div className="companies">
      {error && <div className="error-banner">⚠️ {error}</div>}
      <form className="add-form" onSubmit={handleAdd}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Add company (e.g. GitLab) — ATS auto-detected"
        />
        <button type="submit" className="btn apply">+ Add</button>
      </form>

      <Section title={`🎯 Actively hunted (${active.length})`} items={active}>
        {(c) => (
          <>
            <span className="chip">{c.ats || 'watchlist'}</span>
            <span className="chip">{c.jobs_found} jobs</span>
            <button className="btn ghost" onClick={() => toggle(c, 'enabled', false)}>Pause</button>
            <button className="btn ghost" onClick={() => toggle(c, 'blacklisted', true)}>🚫</button>
          </>
        )}
      </Section>

      <Section title={`👀 Watchlist — discovered, review to hunt (${watchlist.length})`} items={watchlist}>
        {(c) => (
          <>
            <span className="chip">{c.jobs_found} jobs seen</span>
            <button className="btn apply" onClick={() => toggle(c, 'enabled', true)}>Hunt</button>
            <button className="btn ghost" onClick={() => toggle(c, 'blacklisted', true)}>🚫</button>
          </>
        )}
      </Section>

      <Section title={`🚫 Blacklisted (${blacklisted.length})`} items={blacklisted}>
        {(c) => (
          <button className="btn ghost" onClick={() => toggle(c, 'blacklisted', false)}>Unblock</button>
        )}
      </Section>
    </div>
  )
}

function Section({ title, items, children }) {
  return (
    <section className="company-section">
      <h3>{title}</h3>
      {items.length === 0 && <p className="empty-small">none</p>}
      <div className="company-list">
        {items.map((c) => (
          <div className="company-row" key={c.id}>
            <span className="company-name">{c.name}</span>
            <span className="company-actions">{children(c)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
