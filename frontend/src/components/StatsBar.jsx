export default function StatsBar({ stats, scanResult }) {
  const j = stats?.jobs || {}
  const weeks = Array.isArray(stats?.kept_by_week) ? stats.kept_by_week : []
  const maxKept = Math.max(1, ...weeks.map((w) => w.kept))
  return (
    <div className="stats">
      <div className="stat">
        <span className="num">{j.total ?? 0}</span>
        <span className="lbl">Total</span>
      </div>
      <div className="stat tier-a">
        <span className="num">{j.a ?? 0}</span>
        <span className="lbl">A-tier</span>
      </div>
      <div className="stat tier-b">
        <span className="num">{j.b ?? 0}</span>
        <span className="lbl">B-tier</span>
      </div>
      <div className="stat">
        <span className="num">{j.applied ?? 0}</span>
        <span className="lbl">Applied</span>
      </div>
      {weeks.length > 0 && (
        <div className="kept-trend">
          <span className="klabel">Kept/wk</span>
          {weeks.map((w) => (
            <span
              key={w.week}
              className="kept-bar"
              title={`${w.week}: ${w.kept} kept · ${w.a} A-tier · ${w.seen} seen`}
            >
              <i
                className={w.kept === 0 ? 'kept-zero' : undefined}
                style={{
                  height: `${w.kept === 0 ? 2 : Math.max(3, Math.round((w.kept / maxKept) * 26))}px`,
                }}
              />
            </span>
          ))}
        </div>
      )}
      {scanResult && (
        <div className="scan-result">
          Last scan: {scanResult.jobs_found} found · {scanResult.jobs_new} new ·{' '}
          {scanResult.tier_a} A-tier
        </div>
      )}
    </div>
  )
}
