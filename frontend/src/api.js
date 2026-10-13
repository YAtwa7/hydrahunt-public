const base = ''

async function jsonOrThrow(r) {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function fetchJobs(params = {}) {
  const q = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v)),
  )
  return jsonOrThrow(await fetch(`${base}/api/jobs?${q}`))
}

export async function fetchStats() {
  return jsonOrThrow(await fetch(`${base}/api/stats`))
}

export async function startScan() {
  return jsonOrThrow(await fetch(`${base}/api/scan`, { method: 'POST' }))
}

export async function fetchScanStatus() {
  return jsonOrThrow(await fetch(`${base}/api/scan/status`))
}

export async function fetchScans() {
  return jsonOrThrow(await fetch(`${base}/api/scans?limit=10`))
}

export async function setStatus(id, status) {
  return jsonOrThrow(
    await fetch(`${base}/api/jobs/${id}/status?status=${encodeURIComponent(status)}`, {
      method: 'PATCH',
    }),
  )
}

export async function fetchCompanies() {
  return jsonOrThrow(await fetch(`${base}/api/companies`))
}

export async function addCompany(name, careersUrl = '') {
  const q = new URLSearchParams({ name, careers_url: careersUrl })
  return jsonOrThrow(await fetch(`${base}/api/companies?${q}`, { method: 'POST' }))
}

export async function updateCompany(id, patch) {
  const q = new URLSearchParams(
    Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined)),
  )
  return jsonOrThrow(await fetch(`${base}/api/companies/${id}?${q}`, { method: 'PATCH' }))
}

export function logoUrl(company) {
  const slug = (company || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  return slug ? `https://logo.clearbit.com/${slug}.com` : null
}
