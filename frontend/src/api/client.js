/**
 * The backend calls, and nothing else.
 *
 * Each one maps to exactly one endpoint so the cost of a click is obvious at
 * the call site: generatePost() spends an agent run with web search per
 * platform, generateImage() spends one render (branded once per selected
 * brand, stored in Cloudinary), and renderImage() reuses an existing prompt so
 * it spends only the render.
 */

const BASE = import.meta.env.VITE_API_BASE || '/api'
// Only when the backend sets API_KEY. It ships inside the built JS, so it keeps
// out strangers who find the API URL — not anyone who can open this page.
const API_KEY = import.meta.env.VITE_API_KEY

/** GET /brands — names, small logos and which platforms each brand can post to. */
export function getBrands() {
  return call('/brands')
}

/**
 * POST /generate — the expensive one: an agent run with live web search for
 * each ticked platform. Returns { linkedin, meta }, null for an unticked one.
 */
export function generatePost({ niche, linkedin, meta }) {
  return call('/generate', { niche, linkedin, meta })
}

/**
 * POST /image — renders one picture for the text and brands a copy for each
 * of `brands`. Returns { draft_id, images: [{ brand, name, url, download_url,
 * error }], media_type, image_prompt, expires_at }: the images are signed
 * Cloudinary URLs, not bytes. `previousDraftId` is the draft this replaces;
 * the backend deletes it once the new one is stored. Content first: this
 * needs the finished text as input.
 */
export function generateImage({ posts, title, brands, previousDraftId }) {
  return call('/image', imageBody({ posts, title, brands, previousDraftId }))
}

/**
 * POST /image with the prompt filled in — the backend skips the assembly and
 * only re-renders. This is the cheap "try another picture" path.
 */
export function renderImage({ posts, title, brands, previousDraftId, imagePrompt }) {
  return call('/image', {
    ...imageBody({ posts, title, brands, previousDraftId }),
    image_prompt: imagePrompt,
  })
}

/**
 * DELETE /image/{draftId} — "New post": the draft will not be published.
 * Best effort; the daily sweep removes anything this misses.
 */
export function discardImage(draftId) {
  return call(`/image/${draftId}`, undefined, 'DELETE')
}

/**
 * POST /publish — one brand on one platform, only ever called from an
 * explicit approve action.
 *
 * `draftId` names the draft from /image; the backend publishes this brand's
 * copy in it as stored, so what goes out is exactly what the reviewer looked
 * at. Omitted entirely for a text-only post.
 */
export function publishPost({ brand, platform, post, draftId, imageAlt }) {
  return call('/publish', {
    brand,
    platform,
    post,
    draft_id: draftId ?? null,
    image_alt: draftId ? imageAlt : null,
  })
}

/**
 * POST /schedule — one brand, every platform ticked for it, one time.
 * The same approval as publishPost, with a time attached: the text is stored
 * as shown and the brand's draft image is copied out of the drafts folder,
 * then both are published as-is at `scheduledAt`.
 * `scheduledAt` is a Date; it is sent as UTC ISO-8601, which carries its zone.
 */
export function schedulePosts({ brand, scheduledAt, draftId, imageAlt, targets }) {
  return call('/schedule', {
    brand,
    scheduled_at: scheduledAt.toISOString(),
    draft_id: draftId ?? null,
    image_alt: draftId ? imageAlt : null,
    targets,
  })
}

/** GET /schedule — the Scheduled list, newest time first. */
export function listScheduled() {
  return call('/schedule')
}

/** DELETE /schedule/{id} — only while it is still scheduled. */
export function cancelScheduled(id) {
  return call(`/schedule/${id}`, undefined, 'DELETE')
}

/**
 * POST /schedule/{id}/resolve — a person checked the feed for a post whose
 * outcome was unknown, and says whether it is there.
 */
export function resolveScheduled(id, { published, externalUrl }) {
  return call(`/schedule/${id}/resolve`, {
    published,
    external_url: externalUrl || null,
  })
}

function imageBody({ posts, title, brands, previousDraftId }) {
  return {
    linkedin: posts.linkedin?.trim() ? posts.linkedin : null,
    meta: posts.meta?.trim() ? posts.meta : null,
    title,
    brands,
    previous_draft_id: previousDraftId ?? null,
  }
}

async function call(path, body, method = body === undefined ? 'GET' : 'POST') {
  const headers = {}
  if (API_KEY) headers['X-API-Key'] = API_KEY
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (cause) {
    throw new Error(
      'Could not reach the API. Is the backend running on port 8000?',
      { cause },
    )
  }

  const payload = await response.json().catch(() => null)
  if (!response.ok) throw new Error(errorMessage(payload, response.status))
  return payload
}

/**
 * FastAPI answers with `detail` — a string for our own HTTPExceptions, an array
 * of field errors when Pydantic rejects the body. Both are flattened to one
 * readable line rather than dumped as JSON.
 */
function errorMessage(payload, status) {
  const detail = payload?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((e) => `${(e.loc || []).slice(1).join('.') || 'request'}: ${e.msg}`)
      .join('; ')
  }
  return `The API returned HTTP ${status}.`
}
