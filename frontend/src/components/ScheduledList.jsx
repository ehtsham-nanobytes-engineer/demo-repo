import { Button } from './Button'
import { TARGETS } from '../hooks/usePostStudio'

const STATUS_LABEL = {
  scheduled: 'Scheduled',
  publishing: 'Publishing…',
  published: 'Published',
  failed: 'Failed',
  needs_review: 'Check the feed',
  cancelled: 'Cancelled',
}

/**
 * Every scheduled post: its time, status, link and error.
 *
 * Cancel is offered only while a post is still `scheduled`. A `needs_review`
 * post is one whose outcome is unknown (the platform timed out after the
 * request went out), so it is never retried by itself: a person looks at the
 * feed and says whether it is there.
 *
 * Functional only — styling is still to come.
 */
export function ScheduledList({ rows, brands, busy, unavailable, onRefresh, onCancel, onResolve }) {
  if (unavailable) return null

  const brandName = (id) => brands.find((b) => b.id === id)?.name || id
  const platformName = (id) => TARGETS.find((t) => t.id === id)?.label || id

  const resolveAsPublished = (row) => {
    const url = window.prompt('It is on the feed. Paste its link (optional):', '')
    if (url !== null) onResolve(row.id, true, url.trim())
  }

  return (
    <section className="scheduled-list">
      <div className="scheduled-head">
        <strong>Scheduled</strong>
        <Button onClick={onRefresh} disabled={!!busy}>Refresh</Button>
      </div>

      {rows.length === 0 ? (
        <p className="scheduled-empty">Nothing scheduled yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Brand</th>
              <th>Platform</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className={`scheduled-${row.status}`}>
                <td>{new Date(row.scheduled_at).toLocaleString()}</td>
                <td>{brandName(row.brand)}</td>
                <td>{platformName(row.platform)}</td>
                <td>
                  {row.external_url ? (
                    <a href={row.external_url} target="_blank" rel="noreferrer">
                      {STATUS_LABEL[row.status]}
                    </a>
                  ) : (
                    STATUS_LABEL[row.status] || row.status
                  )}
                  {row.error && <div className="scheduled-error">{row.error}</div>}
                </td>
                <td className="scheduled-actions">
                  {row.status === 'scheduled' && (
                    <Button onClick={() => onCancel(row.id)} disabled={!!busy}>
                      Cancel
                    </Button>
                  )}
                  {row.status === 'needs_review' && (
                    <>
                      <Button onClick={() => resolveAsPublished(row)} disabled={!!busy}>
                        It&apos;s live
                      </Button>
                      <Button onClick={() => onResolve(row.id, false)} disabled={!!busy}>
                        Not posted
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
