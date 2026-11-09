import { useEffect, useState } from 'react'
import { BrandPicker } from './BrandPicker'
import { Button } from './Button'
import { CONTENT } from '../hooks/usePostStudio'

const STAGE_LABEL = {
  content: 'Researching and writing…',
  image: 'Drawing the image…',
}

/**
 * The niche input, which platforms to write for, which brands it is for, and
 * the one primary action that starts the whole flow. At least one platform and
 * one brand must be ticked.
 *
 * Once images exist the post is locked: the platform and brand picks freeze
 * and the primary action becomes "New post", which discards the text and the
 * images and unlocks them. It asks for a second click first, because it throws
 * away work (unpublished edits included).
 */
export function NicheBar({
  niche, onChange, platforms, onTogglePlatform, onGenerate, busy, stage, hasPost,
  brands, selectedBrands, onToggleBrand, locked, onNewPost,
}) {
  const [confirming, setConfirming] = useState(false)
  const running = busy === 'all'
  const anyPlatform = Object.values(platforms).some(Boolean)
  const anyBrand = selectedBrands.length > 0
  const ready = niche.trim().length >= 2 && anyPlatform && anyBrand && !busy

  // The confirmation lapses after a few seconds, so a stray click later does
  // not discard anything.
  useEffect(() => {
    if (!confirming) return undefined
    const timer = setTimeout(() => setConfirming(false), 4000)
    return () => clearTimeout(timer)
  }, [confirming])

  useEffect(() => {
    if (!locked) setConfirming(false)
  }, [locked])

  const why = !anyPlatform
    ? 'Tick at least one platform to write for.'
    : !anyBrand
      ? 'Pick at least one brand.'
      : undefined

  return (
    <form
      className="niche-bar"
      onSubmit={(e) => {
        e.preventDefault()
        if (ready && !locked) onGenerate()
      }}
    >
      <label className="niche-label" htmlFor="niche">
        Niche
      </label>
      <input
        id="niche"
        className="niche-input"
        placeholder="e.g. AI in diagnostic labs"
        value={niche}
        onChange={(e) => onChange(e.target.value)}
        disabled={!!busy}
        maxLength={200}
      />
      {locked ? (
        <Button
          type="button"
          variant={confirming ? 'solid' : 'primary'}
          disabled={!!busy}
          onClick={() => {
            if (!confirming) return setConfirming(true)
            setConfirming(false)
            onNewPost()
          }}
          title="Discard this post's text and images and pick brands and platforms again."
        >
          {confirming ? 'Click again to discard' : 'New post'}
        </Button>
      ) : (
        <Button
          type="submit"
          variant="primary"
          loading={running}
          loadingLabel={STAGE_LABEL[stage] || 'Working…'}
          disabled={!ready}
          title={why}
        >
          {hasPost ? 'Generate again' : 'Generate post'}
        </Button>
      )}

      <fieldset className="platform-picks" disabled={!!busy || locked}>
        <legend className="niche-label">Write for</legend>
        {Object.entries(CONTENT).map(([id, spec]) => (
          <label key={id} className={`pick pick-${id} ${platforms[id] ? 'is-on' : ''}`}>
            <input
              type="checkbox"
              checked={platforms[id]}
              onChange={() => onTogglePlatform(id)}
            />
            <span className="pick-box" aria-hidden="true" />
            <span className="pick-text">
              {spec.label}
              <small>{id === 'meta' ? 'Facebook + Instagram caption' : 'Full post'}</small>
            </span>
          </label>
        ))}

        <span className="niche-label picks-divider">For</span>
        <BrandPicker
          brands={brands}
          selected={selectedBrands}
          onToggle={onToggleBrand}
          disabled={!!busy || locked}
          locked={locked}
        />
        {locked && (
          <span className="picks-locked">Locked to this post · New post to change</span>
        )}
      </fieldset>
    </form>
  )
}
