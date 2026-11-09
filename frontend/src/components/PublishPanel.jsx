import { Button } from './Button'
import { TARGETS } from '../hooks/usePostStudio'

/**
 * The approval gate. Nothing above this panel reaches any feed.
 *
 * One row per brand, one checkbox per platform. Ticking a box is the approval
 * for that brand on that platform; the button then publishes every ticked box
 * one at a time. Facebook and Instagram send the Meta caption, LinkedIn sends
 * the LinkedIn post, and every one sends that brand's image exactly as shown —
 * the same stored file, not a re-render.
 *
 * Only the brands picked for this post can be ticked; the others' rows are
 * dimmed. A box that cannot be ticked says why on hover: the brand was not
 * picked, the platform is not configured for it, the text has not been
 * written, or its image failed or expired. A target that already went out —
 * or was scheduled — is locked, so a retry after a partial failure never
 * posts anything twice.
 *
 * "Schedule" is the same approval with a time attached: the ticked boxes are
 * stored exactly as shown and published as-is at that time.
 */
export function PublishPanel({
  brands, postBrands, targets, onToggle, blockedReason, pending, results, busy, disabled,
  onPublish,
  mode, onModeChange, scheduledAt, onScheduledAtChange, onSchedule, scheduleUnavailable,
}) {
  const scheduling = mode === 'schedule'
  const working = busy === (scheduling ? 'schedule' : 'publish')
  const count = `${pending.length} target${pending.length > 1 ? 's' : ''}`

  return (
    <section className="publish-panel">
      <div className="publish-copy">
        <strong>Review, then pick where it goes</strong>
        <p>
          Every box you tick is published exactly as it reads above, edits included,
          with that brand&apos;s image.
        </p>
      </div>

      <div className="publish-grid" role="table" aria-label="Publish targets">
        <div className="publish-row publish-row-head" role="row">
          <span role="columnheader">Brand</span>
          {TARGETS.map((t) => (
            <span key={t.id} role="columnheader" className={`platform-${t.id}`}>
              {t.label}
            </span>
          ))}
        </div>

        {brands.map((brand) => (
          <div
            key={brand.id}
            className={`publish-row ${postBrands.includes(brand.id) ? '' : 'is-excluded'}`}
            role="row"
          >
            <span className="publish-brand" role="rowheader">
              {brand.logo_base64 ? (
                <img
                  className="publish-logo"
                  src={`data:image/png;base64,${brand.logo_base64}`}
                  alt=""
                />
              ) : (
                <span className="publish-logo publish-logo-missing" aria-hidden="true">
                  {brand.name.slice(0, 2)}
                </span>
              )}
              {brand.name}
            </span>

            {TARGETS.map((t) => {
              const key = `${brand.id}:${t.id}`
              const result = results[key]
              const done = result?.status === 'done' || result?.status === 'scheduled'
              const reason = done ? null : blockedReason(brand, t.id)
              const checked = done || (!reason && !!targets[brand.id]?.[t.id])
              return (
                <span key={t.id} role="cell" className="publish-cell">
                  <label
                    className={`publish-check ${reason ? 'is-blocked' : ''}`}
                    title={reason || `Publish to ${brand.name} on ${t.label}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled || done || !!reason}
                      onChange={() => onToggle(brand.id, t.id)}
                      aria-label={`${brand.name} on ${t.label}`}
                    />
                    <span className="pick-box" aria-hidden="true" />
                  </label>
                  <Status result={result} />
                </span>
              )
            })}
          </div>
        ))}
      </div>

      {Object.entries(results).some(([, r]) => r.status === 'failed') && (
        <ul className="publish-failures">
          {Object.entries(results)
            .filter(([, r]) => r.status === 'failed')
            .map(([key, r]) => {
              const [brandId, platform] = key.split(':')
              const brand = brands.find((b) => b.id === brandId)
              const target = TARGETS.find((t) => t.id === platform)
              return (
                <li key={key}>
                  <strong>{brand?.name} · {target?.label}:</strong> {r.error}
                </li>
              )
            })}
        </ul>
      )}

      <div className="publish-actions">
        <fieldset className="publish-when" disabled={disabled}>
          <legend className="sr-only">When</legend>
          <label>
            <input
              type="radio"
              name="publish-when"
              checked={!scheduling}
              onChange={() => onModeChange('now')}
            />
            Publish now
          </label>
          <label title={scheduleUnavailable || 'Publish at a time you pick'}>
            <input
              type="radio"
              name="publish-when"
              checked={scheduling}
              disabled={!!scheduleUnavailable}
              onChange={() => onModeChange('schedule')}
            />
            Schedule
          </label>
          {scheduling && (
            <input
              type="datetime-local"
              value={scheduledAt}
              onChange={(e) => onScheduledAtChange(e.target.value)}
              aria-label="Publish at"
            />
          )}
        </fieldset>

        <Button
          variant="primary"
          onClick={scheduling ? onSchedule : onPublish}
          loading={working}
          loadingLabel={scheduling ? 'Scheduling…' : 'Publishing…'}
          disabled={disabled || pending.length === 0 || (scheduling && !scheduledAt)}
        >
          {!pending.length
            ? 'Tick where to publish'
            : scheduling
              ? `Approve & schedule ${count}`
              : `Approve & publish to ${count}`}
        </Button>
      </div>
    </section>
  )
}

function Status({ result }) {
  if (!result) return null
  if (result.status === 'publishing') return <span className="status status-busy">Posting…</span>
  if (result.status === 'scheduled') return <span className="status status-done">⏱ Scheduled</span>
  if (result.status === 'failed') {
    return (
      <span className="status status-failed" title={result.error}>
        Failed
      </span>
    )
  }
  return result.url ? (
    <a className="status status-done" href={result.url} target="_blank" rel="noreferrer">
      ✓ View
    </a>
  ) : (
    <span className="status status-done">✓ Posted</span>
  )
}
