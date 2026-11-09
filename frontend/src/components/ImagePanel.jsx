import { useEffect, useState } from 'react'
import { BrandLoader } from './BrandLoader'
import { Button } from './Button'

/**
 * The generated visual, once per selected brand, and the controls that make
 * another one.
 *
 * One picture is rendered and each selected brand gets its own copy with its
 * own logo, so the cards differ only in the mark — which is exactly what is
 * being reviewed here. Each copy is a draft in Cloudinary and is shown from
 * its signed URL; that file is byte for byte what gets published. The frames
 * hold the 1.91:1 feed ratio so what is reviewed is framed the way the feeds
 * will frame it.
 */
export function ImagePanel({
  image, brands, postBrands, expired, hasPost, busy, stage, disabled, onGenerate,
  onRerender,
}) {
  const [briefOpen, setBriefOpen] = useState(false)
  const [showBrief, setShowBrief] = useState(false)
  const [draft, setDraft] = useState('')
  const cards = image?.images || []
  const columns = Math.max(1, Math.min(3, cards.length))
  const coming = brands.filter((b) => postBrands.includes(b.id)).map((b) => b.name)
  const working =
    busy === 'image' || busy === 'render' || (busy === 'all' && stage === 'image')

  // Whatever the model last wrote is the starting point for an edit — it is
  // easier to fix one clause than to write a brief from nothing.
  useEffect(() => {
    if (image?.prompt) setDraft(image.prompt)
  }, [image?.prompt])

  const changed = image ? draft.trim() !== image.prompt.trim() : draft.trim().length > 0

  return (
    <section className="panel">
      <div className="panel-head">
        <h2 className="panel-title">
          <span className="dot" /> Image
        </h2>
        {image && (
          <span className="tag">
            One picture · {cards.length} brand{cards.length > 1 ? 's' : ''} · 1200 × 627
          </span>
        )}
      </div>

      {working && (
        <div className="image-frame image-frame-wide">
          <div className="image-working">
            <BrandLoader
              size="lg"
              label={busy === 'render' ? 'Rendering…' : 'Drawing the image…'}
              sub={`One render, then the logo for ${coming.join(', ') || 'each brand'}`}
            />
          </div>
        </div>
      )}

      {!working && expired && (
        <p className="hint hint-warn">
          These images have expired and may already be deleted. Regenerate the image
          to publish or schedule.
        </p>
      )}

      {!working && !image && (
        <div className="image-frame image-frame-wide">
          <div className="image-placeholder">
            <Grid />
            <strong>{hasPost ? 'No image yet' : 'Content comes first'}</strong>
            <p>
              {hasPost
                ? coming.length
                  ? `Generate one for ${coming.join(', ')}, or write your own brief.`
                  : 'Pick at least one brand above first.'
                : 'The image is drawn from the finished text, so it cannot be made before the content exists.'}
            </p>
          </div>
        </div>
      )}

      {!working && image && (
        <div className="brand-images" style={{ '--columns': columns }}>
          {cards.map((card) => (
            <figure key={card.brand} className="brand-image">
              <div className="image-frame">
                {card.url ? (
                  <img
                    className="image"
                    src={card.url}
                    alt={`Generated visual with the ${card.name} logo`}
                  />
                ) : (
                  <div className="image-placeholder image-placeholder-error">
                    <strong>Not branded</strong>
                    <p>{card.error || 'This brand has no image.'}</p>
                  </div>
                )}
              </div>
              <figcaption>
                <span>{card.name}</span>
                {card.downloadUrl && <a href={card.downloadUrl}>Download</a>}
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      <div className="panel-actions">
        <Button
          variant="solid"
          onClick={onGenerate}
          loading={busy === 'image'}
          loadingLabel="Drawing…"
          disabled={disabled || !hasPost || !postBrands.length}
          title="Writes a fresh brief from the current post, then renders it for the same brands."
        >
          {image ? 'Regenerate image' : 'Generate image'}
        </Button>

        {image && (
          <Button
            onClick={() => onRerender()}
            loading={busy === 'render' && !changed}
            loadingLabel="Rendering…"
            disabled={disabled}
            title="Renders the same brief again for a different take."
          >
            Another take
            <span className="btn-note">same brief</span>
          </Button>
        )}

        <Button
          onClick={() => setBriefOpen((v) => !v)}
          disabled={disabled || !hasPost}
          aria-expanded={briefOpen}
        >
          {briefOpen ? 'Hide brief' : 'Write your own brief'}
        </Button>
      </div>

      {/* Read-only view of the brief behind the picture on screen. Separate
          from the editor above: this one answers "why did it draw that?"
          without putting the text into an input the user might change by
          accident. */}
      {image && (
        <div className="brief">
          <button
            className="brief-toggle"
            onClick={() => setShowBrief((v) => !v)}
            aria-expanded={showBrief}
          >
            {showBrief ? 'Hide' : 'Show'} the brief the model was given
          </button>
          {showBrief && <p className="brief-text">{image.prompt}</p>}
        </div>
      )}

      {briefOpen && (
        <div className="brief-editor">
          <label className="field-label" htmlFor="brief">
            Image brief
            <span className="field-hint">Sent straight to the image model.</span>
          </label>
          <textarea
            id="brief"
            className="brief-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="One subject, one setting. e.g. A single unopened wooden crate on a concrete floor in a wide empty warehouse, one shaft of afternoon light across it, documentary photograph, muted palette, no text anywhere in the image"
            rows={4}
            spellCheck="false"
          />
          <div className="brief-editor-actions">
            <Button
              variant="solid"
              onClick={() => onRerender(draft)}
              loading={busy === 'render'}
              loadingLabel="Rendering…"
              disabled={disabled || !draft.trim() || !postBrands.length}
            >
              Render this brief
            </Button>
            {image && changed && (
              <Button onClick={() => setDraft(image.prompt)} disabled={disabled}>
                Reset to the model&apos;s
              </Button>
            )}
            <p className="brief-tip">
              Keep it to one subject. Lists of objects, screens and anything made of
              writing come out as mush.
            </p>
          </div>
        </div>
      )}
    </section>
  )
}

/** The square-and-circle grid from the logo mark, as an empty-state glyph. */
function Grid() {
  return (
    <div className="empty-grid" aria-hidden="true">
      {Array.from({ length: 12 }, (_, i) => (
        <span key={i} className={i % 3 === 0 ? 'sq' : 'ci'} />
      ))}
    </div>
  )
}
