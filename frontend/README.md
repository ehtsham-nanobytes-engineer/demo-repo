# Post Studio — frontend

React + Vite UI for the LinkedIn post service. Theme is taken from the logo:
the orange of the dot grid, the near-black of the wordmark, white behind both.

## Running it

Node 18+ is required (it is not currently installed on this machine — get it
from nodejs.org).

```bash
# 1. backend, from the project root
uvicorn app.main:app --reload

# 2. frontend, from this folder
npm install
npm run dev          # http://localhost:5173
```

Vite proxies `/api/*` to `127.0.0.1:8000`, so in dev the browser talks to a
single origin. Set `VITE_API_BASE` when the API lives somewhere else.

## The flow

Generate is one click that makes two calls — `/generate` writes the post, then
`/image` draws the visual from that finished text. After that, each control is
scoped so a click only spends what it has to:

| Control | Calls | Cost |
| --- | --- | --- |
| **Generate post** | `/generate` then `/image` for the picked brands | agent run with web search + render |
| **New post** | `DELETE /image/{id}` | — (discards the draft, unlocks the picks) |
| **Regenerate content** | `/generate` | agent run with web search. Image untouched. |
| **Regenerate image** | `/image` with `previous_draft_id` | render only. Same brands. Text untouched. |
| **Another take** | `/image` with `image_prompt` | render only |
| **Write your own brief** | `/image` with `image_prompt` | render only |
| **Approve & publish** | `/publish` | — |
| **Approve & schedule** | `/schedule`, once per brand | — |
| **Cancel** / **It's live** / **Not posted** | `DELETE /schedule/{id}` / `POST /schedule/{id}/resolve` | — |

"Another take" is the plain retry: it sends the existing prompt back and only
re-renders. Reach for it when the idea is right and the picture is not.

**Write your own brief** opens the prompt that was sent — the standing art
direction followed by the post — editable, and sends it back as-is. It is the
override for when the model keeps missing the point. One subject, one setting — lists of objects and anything made of writing
come out as mush.

## Files

```
src/
  api/client.js            one function per endpoint, errors flattened to a line
  hooks/usePostStudio.js   all state and every action
  components/              Header, NicheBar, PostPanel, ImagePanel, PublishPanel,
                           ScheduledList, Button, Banner, BrandLoader, ThemeToggle
  hooks/useTheme.js        light / dark / system, remembered per browser
  styles/theme.css         both palettes, keyed off the logo and LinkedIn blue
  styles/app.css           layout and components
```

## Theme

Light, dark, or system — the toggle in the header cycles them and the choice is
kept in `localStorage`. "System" writes no attribute and lets the media query
decide; an explicit choice stamps `data-theme` on `<html>` and those rules are
written to beat the media query in both directions.

The logo ships as **two files**. `logo.png` is the mark keyed off its white JPEG
background; `logo-dark.png` lifts only the neutral ink and leaves the brand
orange untouched. A CSS `invert()` filter was the obvious approach and is wrong:
it drags `#ff983c` to a muddy `#c24d00`. Which file shows is decided in CSS, so
the system theme needs no JS.

## Notes

- Brands are picked in the dropdown next to the platform toggles, before
  generating. Only those brands get an image and can be ticked for publishing.
  Once images exist the brand and platform picks lock; **New post** (two clicks,
  it discards work) unlocks them.
- Each brand's image is a draft in Cloudinary, shown from its signed URL.
  Drafts expire after 24 hours; an expired one blocks publishing with a
  "regenerate" message.
- `/publish` and `/schedule` send the approved text and the `draft_id`, never
  image bytes. The backend publishes that brand's stored copy, exactly as shown.
- The post textarea is the human review gate. Nothing is published until
  Approve is clicked.
