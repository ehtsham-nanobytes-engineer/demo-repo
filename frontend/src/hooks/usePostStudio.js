import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cancelScheduled,
  discardImage,
  generateImage,
  generatePost,
  getBrands,
  listScheduled,
  publishPost,
  renderImage,
  resolveScheduled,
  schedulePosts,
} from '../api/client'

/** The two kinds of text, and the longest each feed accepts. */
export const CONTENT = {
  linkedin: { label: 'LinkedIn', max: 3000, noun: 'post' },
  meta: { label: 'Meta', max: 2200, noun: 'caption' },
}

/** The publish targets per brand, and which text each one sends. */
export const TARGETS = [
  { id: 'facebook', label: 'Facebook', content: 'meta' },
  { id: 'instagram', label: 'Instagram', content: 'meta' },
  { id: 'linkedin', label: 'LinkedIn', content: 'linkedin' },
]

const EMPTY_POSTS = { linkedin: '', meta: '' }

/** A result that has left the studio: published now, or handed to the scheduler. */
const SETTLED = new Set(['done', 'scheduled'])

/**
 * All of the studio's state and every action the UI can take.
 *
 * The rule the backend enforces with a schema is mirrored here in the UI: there
 * is no path to an image without text first. Every image action reads `posts`,
 * and the buttons that trigger them are disabled while both are empty.
 *
 * Brands are picked before generating, and only those brands get an image and
 * can be published to. Once images exist the post is **locked**: the brand and
 * platform picks are frozen, and the main button becomes "New post", which
 * discards the draft and unlocks them. Regenerating the image keeps the same
 * brands.
 *
 * Images live in Cloudinary as one draft per render. Each new render replaces
 * (and deletes) the previous draft; publishing refers to it by `draftId`, so no
 * image bytes travel through the browser.
 *
 * Each action is deliberately one API call (or, for the full run, two), so a
 * click never costs more than the button says it does. Publishing is the one
 * exception — one call per ticked checkbox, so every target reports on its own.
 */
export function usePostStudio() {
  const [niche, setNiche] = useState('')
  const [platforms, setPlatforms] = useState({ linkedin: true, meta: true })
  const [selectedBrands, setSelectedBrands] = useState([]) // brand ids to generate for
  const [posts, setPosts] = useState(EMPTY_POSTS)
  // { draftId, images: [{ brand, name, url, downloadUrl, error }], mediaType,
  //   prompt, expiresAt } — one branded copy per selected brand.
  const [image, setImage] = useState(null)
  const [brands, setBrands] = useState([])
  const [targets, setTargets] = useState({}) // { [brand]: { [platform]: bool } }
  const [results, setResults] = useState({}) // { 'brand:platform': { status, url, error } }
  const [busy, setBusy] = useState(null) // which action is running
  const [stage, setStage] = useState(null) // sub-step of the full run
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  // Publish now, or approve for a time. Both are the same approval gate.
  const [mode, setMode] = useState('now') // 'now' | 'schedule'
  const [scheduledAt, setScheduledAt] = useState(defaultScheduleTime) // datetime-local value
  const [scheduled, setScheduled] = useState([]) // rows from GET /schedule
  // Why scheduling cannot be used (the backend has no DATABASE_URL), or null.
  const [scheduleUnavailable, setScheduleUnavailable] = useState(null)
  // Re-renders once a minute so an expiring draft locks its checkboxes on time.
  const [now, setNow] = useState(Date.now)

  // What the API last returned, so an edit can be detected without another
  // piece of state to keep in sync.
  const generated = useRef(EMPTY_POSTS)
  const edited = {
    linkedin: posts.linkedin !== generated.current.linkedin,
    meta: posts.meta !== generated.current.meta,
  }
  const hasPost = !!(posts.linkedin.trim() || posts.meta.trim())
  const locked = !!image
  // The brands this post is for: frozen to the ones that were rendered once
  // there is an image, the live selection before that.
  const postBrands = useMemo(
    () => (image ? image.images.map((i) => i.brand) : selectedBrands),
    [image, selectedBrands],
  )
  const expired = !!image && now > Date.parse(image.expiresAt)

  useEffect(() => {
    getBrands()
      .then((list) => {
        setBrands(list)
        // Every brand that can be branded, by default. One with no logo file
        // cannot get an image, so it starts unticked (and cannot be ticked).
        setSelectedBrands(list.filter((b) => b.logo_base64).map((b) => b.id))
      })
      .catch((err) => setError(`Could not load the brands: ${err.message}`))
  }, [])

  useEffect(() => {
    if (!image) return undefined
    const timer = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(timer)
  }, [image])

  const refreshScheduled = useCallback(
    () =>
      listScheduled()
        .then((rows) => {
          setScheduled(rows)
          setScheduleUnavailable(null)
        })
        .catch((err) => setScheduleUnavailable(err.message)),
    [],
  )

  useEffect(() => {
    refreshScheduled()
  }, [refreshScheduled])

  /** The hook of the post doubles as the headline the image is drawn against. */
  const title = useMemo(() => titleOf(posts), [posts])

  const run = useCallback(async (key, work) => {
    setBusy(key)
    setError(null)
    setNotice(null)
    try {
      await work()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
      setStage(null)
    }
  }, [])

  /** Write the text for the platforms in `which`; the others are left alone. */
  const fetchPosts = useCallback(
    async (which) => {
      setStage('content')
      const data = await generatePost({ niche: niche.trim(), ...which })
      const next = { ...posts }
      for (const key of Object.keys(CONTENT)) {
        if (which[key] && data[key] != null) next[key] = data[key]
      }
      generated.current = { ...generated.current, ...pick(next, which) }
      setPosts(next)
      setResults({})
      return next
    },
    [niche, posts],
  )

  /**
   * Render for `brandIds`, replacing the draft on screen (if any). The backend
   * deletes the old draft only once the new one is stored, so a failed render
   * leaves the current images as they were.
   */
  const fetchImage = useCallback(
    async (text, brandIds, imagePrompt) => {
      setStage('image')
      const request = {
        posts: text,
        title: titleOf(text),
        brands: brandIds,
        previousDraftId: image?.draftId,
      }
      const data = imagePrompt
        ? await renderImage({ ...request, imagePrompt })
        : await generateImage(request)
      setImage({
        draftId: data.draft_id,
        images: data.images.map((i) => ({
          brand: i.brand,
          name: i.name,
          url: i.url,
          downloadUrl: i.download_url,
          error: i.error,
        })),
        mediaType: data.media_type,
        prompt: data.image_prompt,
        expiresAt: data.expires_at,
      })
      setNow(Date.now())
      setResults({})
    },
    [image],
  )

  /** The main button: research and write, then draw. Two calls, one click. */
  const generateEverything = useCallback(
    () =>
      run('all', async () => {
        if (!selectedBrands.length) throw new Error('Pick at least one brand.')
        const text = await fetchPosts(platforms)
        await fetchImage(text, selectedBrands)
      }),
    [run, fetchPosts, fetchImage, platforms, selectedBrands],
  )

  /**
   * "New post": throw away the text and the images and unlock the picks. The
   * draft is deleted in the background; if that fails the daily sweep gets it,
   * so the user is never held up or shown an error for it.
   */
  const newPost = useCallback(() => {
    if (image?.draftId) discardImage(image.draftId).catch(() => {})
    generated.current = EMPTY_POSTS
    setPosts(EMPTY_POSTS)
    setImage(null)
    setTargets({})
    setResults({})
    setError(null)
    setNotice(null)
  }, [image])

  /** New words for one platform, same picture. Does not touch the image. */
  const regenerateContent = useCallback(
    (platform) =>
      run(`content:${platform}`, async () => {
        await fetchPosts({ linkedin: false, meta: false, [platform]: true })
        setNotice(
          image
            ? 'New content. The image still illustrates the previous version — regenerate it if it no longer fits.'
            : null,
        )
      }),
    [run, fetchPosts, image],
  )

  /** A new picture for the text as it currently reads, for the same brands. */
  const regenerateImage = useCallback(
    () =>
      run('image', async () => {
        if (!postBrands.length) throw new Error('Pick at least one brand.')
        await fetchImage(posts, postBrands)
      }),
    [run, fetchImage, posts, postBrands],
  )

  /**
   * Render a prompt directly. With no argument it reuses the last prompt — the
   * cheapest way to ask for another take. With one, it renders whatever the
   * user typed, the manual override when the picture keeps missing the point.
   */
  const rerenderImage = useCallback(
    (customPrompt) =>
      run('render', async () => {
        const prompt = (customPrompt || '').trim() || image?.prompt
        if (!prompt) throw new Error('There is no prompt to render.')
        if (!postBrands.length) throw new Error('Pick at least one brand.')
        await fetchImage(posts, postBrands, prompt)
      }),
    [run, fetchImage, posts, image, postBrands],
  )

  /** Why a checkbox cannot be ticked right now, or null when it can. */
  const blockedReason = useCallback(
    (brand, platform) => {
      if (!postBrands.includes(brand.id)) {
        return locked
          ? 'Not selected for this post — click New post to pick different brands'
          : 'Not selected — add it in the brand picker above'
      }
      const target = TARGETS.find((t) => t.id === platform)
      const status = brand.platforms?.[platform]
      if (!status?.ready) return status?.reason || 'Not configured'
      const text = posts[target.content]
      const spec = CONTENT[target.content]
      if (!text.trim()) return `Generate the ${spec.label} ${spec.noun} first`
      if (text.length > (platform === 'instagram' ? 2200 : spec.max)) {
        return `The ${spec.label} ${spec.noun} is over the ${platform} limit`
      }
      if (image) {
        const own = image.images.find((i) => i.brand === brand.id)
        if (!own?.url) return own?.error || 'This brand has no image'
        if (expired) return 'The image expired — regenerate it'
      }
      if (platform === 'instagram' && !image) return 'Instagram needs the image'
      return null
    },
    [posts, image, postBrands, locked, expired],
  )

  /** Tick or untick a brand in the picker. Ignored once the post is locked. */
  const toggleBrand = useCallback(
    (brandId) => {
      if (locked) return
      setSelectedBrands((prev) =>
        prev.includes(brandId) ? prev.filter((id) => id !== brandId) : [...prev, brandId],
      )
    },
    [locked],
  )

  const toggleTarget = useCallback((brandId, platform) => {
    setTargets((prev) => ({
      ...prev,
      [brandId]: { ...prev[brandId], [platform]: !prev[brandId]?.[platform] },
    }))
  }, [])

  /** Every ticked, currently-allowed target that has not gone out yet. */
  const pending = useMemo(
    () =>
      brands.flatMap((brand) =>
        TARGETS.filter(
          (t) =>
            targets[brand.id]?.[t.id] &&
            !blockedReason(brand, t.id) &&
            !SETTLED.has(results[`${brand.id}:${t.id}`]?.status),
        ).map((t) => ({ brand, target: t })),
      ),
    [brands, targets, results, blockedReason],
  )

  /** The draft to send for a brand: only when that brand has an image in it. */
  const draftFor = useCallback(
    (brandId) =>
      image?.images.some((i) => i.brand === brandId && i.url) ? image.draftId : undefined,
    [image],
  )

  /** Checked right before sending, since the minute timer can lag a little. */
  const assertFresh = useCallback(() => {
    if (image && Date.now() > Date.parse(image.expiresAt)) {
      setNow(Date.now())
      throw new Error('The image expired. Regenerate it, then approve again.')
    }
  }, [image])

  /**
   * Publish exactly what is on screen to every pending target, one at a time.
   * A target that already went out is never sent again, so retrying after a
   * partial failure only retries what failed.
   */
  const publish = useCallback(
    () =>
      run('publish', async () => {
        assertFresh()
        let failed = 0
        for (const { brand, target } of pending) {
          const key = `${brand.id}:${target.id}`
          setResults((prev) => ({ ...prev, [key]: { status: 'publishing' } }))
          try {
            const { url } = await publishPost({
              brand: brand.id,
              platform: target.id,
              post: posts[target.content],
              draftId: draftFor(brand.id),
              imageAlt: altText(posts, title),
            })
            setResults((prev) => ({ ...prev, [key]: { status: 'done', url } }))
          } catch (err) {
            failed += 1
            setResults((prev) => ({ ...prev, [key]: { status: 'failed', error: err.message } }))
          }
        }
        if (failed) setError(`${failed} target(s) failed — see below. Publish again to retry only those.`)
        else setNotice('Published to every selected target.')
      }),
    [run, assertFresh, pending, draftFor, posts, title],
  )

  /**
   * Approve every pending target for the picked time. One POST /schedule per
   * brand, carrying that brand's ticked platforms and its draft. The backend
   * copies the brand's image out of the drafts folder, so it survives the
   * draft sweep; nothing is generated later.
   */
  const schedule = useCallback(
    () =>
      run('schedule', async () => {
        assertFresh()
        const at = new Date(scheduledAt)
        if (Number.isNaN(at.getTime())) throw new Error('Pick a date and time first.')
        if (at.getTime() < Date.now() + 60_000) {
          throw new Error('Pick a time at least a minute from now.')
        }

        const byBrand = new Map()
        for (const item of pending) {
          if (!byBrand.has(item.brand.id)) byBrand.set(item.brand.id, [])
          byBrand.get(item.brand.id).push(item)
        }

        let failed = 0
        for (const [brandId, items] of byBrand) {
          const keys = items.map(({ target }) => `${brandId}:${target.id}`)
          const mark = (result) =>
            setResults((prev) => ({
              ...prev,
              ...Object.fromEntries(keys.map((k) => [k, result])),
            }))
          mark({ status: 'publishing' })
          try {
            await schedulePosts({
              brand: brandId,
              scheduledAt: at,
              draftId: draftFor(brandId),
              imageAlt: altText(posts, title),
              targets: items.map(({ target }) => ({
                platform: target.id,
                post: posts[target.content],
              })),
            })
            mark({ status: 'scheduled' })
          } catch (err) {
            failed += 1
            mark({ status: 'failed', error: err.message })
          }
        }
        await refreshScheduled()
        if (failed) setError(`${failed} brand(s) could not be scheduled — see below.`)
        else setNotice(`Scheduled for ${at.toLocaleString()}.`)
      }),
    [run, assertFresh, pending, draftFor, posts, title, scheduledAt, refreshScheduled],
  )

  const cancel = useCallback(
    (id) =>
      run(`cancel:${id}`, async () => {
        await cancelScheduled(id)
        await refreshScheduled()
      }),
    [run, refreshScheduled],
  )

  /** For a needs_review row, after a person has looked at the feed. */
  const resolve = useCallback(
    (id, published, externalUrl) =>
      run(`resolve:${id}`, async () => {
        await resolveScheduled(id, { published, externalUrl })
        await refreshScheduled()
      }),
    [run, refreshScheduled],
  )

  const setPost = useCallback(
    (platform, text) => setPosts((prev) => ({ ...prev, [platform]: text })),
    [],
  )
  const revertEdits = useCallback(
    (platform) => setPosts((prev) => ({ ...prev, [platform]: generated.current[platform] })),
    [],
  )
  const togglePlatform = useCallback(
    (platform) => {
      if (locked) return
      setPlatforms((prev) => ({ ...prev, [platform]: !prev[platform] }))
    },
    [locked],
  )

  return {
    niche, setNiche, platforms, togglePlatform,
    selectedBrands, toggleBrand, postBrands, locked, expired, newPost,
    posts, setPost, title, edited, revertEdits, hasPost,
    image, brands, targets, toggleTarget, blockedReason, pending, results,
    busy, stage, error, notice, setError, setNotice,
    generateEverything, regenerateContent, regenerateImage, rerenderImage, publish,
    mode, setMode, scheduledAt, setScheduledAt, schedule,
    scheduled, scheduleUnavailable, refreshScheduled, cancel, resolve,
  }
}

/** An hour from now, on the minute, in the browser's zone — a datetime-local value. */
function defaultScheduleTime() {
  const at = new Date(Date.now() + 60 * 60 * 1000)
  at.setSeconds(0, 0)
  const pad = (n) => String(n).padStart(2, '0')
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
    `T${pad(at.getHours())}:${pad(at.getMinutes())}`
}

function titleOf(posts) {
  const text = posts.linkedin?.trim() ? posts.linkedin : posts.meta || ''
  return text.split('\n').find((line) => line.trim())?.trim().slice(0, 300) || ''
}

/** Alt text for the image: the short caption if there is one, else the hook. */
function altText(posts, title) {
  return (posts.meta.trim() || title).slice(0, 300)
}

function pick(obj, which) {
  return Object.fromEntries(Object.keys(obj).filter((k) => which[k]).map((k) => [k, obj[k]]))
}
