import { BrandLoader } from './BrandLoader'
import { Button } from './Button'
import { CONTENT } from '../hooks/usePostStudio'

/**
 * One platform's text: editable, counted against that feed's limit, with its
 * own controls. Rendered once for LinkedIn and once for Meta.
 */
export function PostPanel({
  platform, post, onChange, edited, onRevert, onRegenerate, busy, stage, disabled,
  canWrite,
}) {
  const spec = CONTENT[platform]
  const writing = busy === `content:${platform}` || (busy === 'all' && stage === 'content')
  const count = post.length
  const over = count > spec.max
  const near = !over && count > spec.max * 0.9

  return (
    <section className={`panel panel-${platform}`}>
      <div className="panel-head">
        <h2 className="panel-title">
          <span className="dot" /> {spec.label} {spec.noun}
          {platform === 'meta' && <span className="tag">Facebook · Instagram</span>}
        </h2>
        <div className="panel-meta">
          {edited && <span className="tag tag-edit">Edited</span>}
          <span className={`counter ${over ? 'is-over' : near ? 'is-near' : ''}`}>
            {count.toLocaleString()} / {spec.max.toLocaleString()}
          </span>
        </div>
      </div>

      {writing ? (
        <div className="panel-loading">
          <BrandLoader
            size="lg"
            label={platform === 'meta' ? 'Writing the caption…' : 'Researching and writing…'}
          />
        </div>
      ) : post ? (
        <>
          <textarea
            className={`post-editor ${platform === 'meta' ? 'post-editor-short' : ''}`}
            value={post}
            onChange={(e) => onChange(e.target.value)}
            spellCheck="false"
            aria-label={`${spec.label} ${spec.noun} text`}
          />
          {over && (
            <p className="hint hint-warn">
              Over the {spec.max.toLocaleString()}-character limit — trim it before
              publishing.
            </p>
          )}
          <div className="panel-actions">
            <Button
              variant="solid"
              onClick={onRegenerate}
              loading={busy === `content:${platform}`}
              loadingLabel="Rewriting…"
              disabled={disabled || !canWrite}
              title="One agent run with live web search. Leaves the image alone."
            >
              Regenerate {spec.noun}
            </Button>
            <Button onClick={() => navigator.clipboard?.writeText(post)} disabled={disabled}>
              Copy text
            </Button>
            {edited && (
              <Button onClick={onRevert} disabled={disabled}>
                Revert edits
              </Button>
            )}
          </div>
        </>
      ) : (
        <div className="empty empty-compact">
          <strong>No {spec.label} {spec.noun} yet</strong>
          <p>
            {platform === 'meta'
              ? 'A one-to-two line caption for Facebook and Instagram, written from the same story.'
              : 'Enter a niche above and hit Generate. The agent researches it live and writes the post.'}
          </p>
          <Button variant="solid" onClick={onRegenerate} disabled={disabled || !canWrite}>
            Write it now
          </Button>
        </div>
      )}
    </section>
  )
}
