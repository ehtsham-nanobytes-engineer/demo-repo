import { Banner } from './components/Banner'
import { Header } from './components/Header'
import { ImagePanel } from './components/ImagePanel'
import { NicheBar } from './components/NicheBar'
import { PostPanel } from './components/PostPanel'
import { PublishPanel } from './components/PublishPanel'
import { ScheduledList } from './components/ScheduledList'
import { CONTENT, usePostStudio } from './hooks/usePostStudio'
import { useTheme } from './hooks/useTheme'
import './styles/theme.css'
import './styles/app.css'

export default function App() {
  const studio = usePostStudio()
  const { theme, cycle } = useTheme()
  const { busy } = studio
  const locked = !!busy // one call at a time; everything else waits
  const canWrite = studio.niche.trim().length >= 2

  // A platform's panel shows once it is ticked or already has text, so
  // unticking Meta after generating does not throw the caption away.
  const shown = Object.keys(CONTENT).filter(
    (id) => studio.platforms[id] || studio.posts[id],
  )

  return (
    <div className="app">
      <Header theme={theme} onCycleTheme={cycle} />

      <main className="main">
        <NicheBar
          niche={studio.niche}
          onChange={studio.setNiche}
          platforms={studio.platforms}
          onTogglePlatform={studio.togglePlatform}
          onGenerate={studio.generateEverything}
          busy={busy}
          stage={studio.stage}
          hasPost={studio.hasPost}
          brands={studio.brands}
          selectedBrands={studio.selectedBrands}
          onToggleBrand={studio.toggleBrand}
          locked={studio.locked}
          onNewPost={studio.newPost}
        />

        <Banner tone="error" onDismiss={() => studio.setError(null)}>
          {studio.error}
        </Banner>
        <Banner tone="info" onDismiss={() => studio.setNotice(null)}>
          {studio.notice}
        </Banner>

        {/* Content first, image second — the order the work happens in. */}
        <div className={`post-panels post-panels-${shown.length}`}>
          {shown.map((id) => (
            <PostPanel
              key={id}
              platform={id}
              post={studio.posts[id]}
              onChange={(text) => studio.setPost(id, text)}
              edited={studio.edited[id]}
              onRevert={() => studio.revertEdits(id)}
              onRegenerate={() => studio.regenerateContent(id)}
              busy={busy}
              stage={studio.stage}
              disabled={locked}
              canWrite={canWrite}
            />
          ))}
        </div>

        <ImagePanel
          image={studio.image}
          brands={studio.brands}
          postBrands={studio.postBrands}
          expired={studio.expired}
          hasPost={studio.hasPost}
          busy={busy}
          stage={studio.stage}
          disabled={locked}
          onGenerate={studio.regenerateImage}
          onRerender={studio.rerenderImage}
        />

        {studio.hasPost && (
          <PublishPanel
            brands={studio.brands}
            postBrands={studio.postBrands}
            targets={studio.targets}
            onToggle={studio.toggleTarget}
            blockedReason={studio.blockedReason}
            pending={studio.pending}
            results={studio.results}
            busy={busy}
            disabled={locked}
            onPublish={studio.publish}
            mode={studio.mode}
            onModeChange={studio.setMode}
            scheduledAt={studio.scheduledAt}
            onScheduledAtChange={studio.setScheduledAt}
            onSchedule={studio.schedule}
            scheduleUnavailable={studio.scheduleUnavailable}
          />
        )}

        <ScheduledList
          rows={studio.scheduled}
          brands={studio.brands}
          busy={busy}
          unavailable={studio.scheduleUnavailable}
          onRefresh={studio.refreshScheduled}
          onCancel={studio.cancel}
          onResolve={studio.resolve}
        />
      </main>

      <footer className="footer">
        Every post and image is reviewed by a person before it goes out.
      </footer>
    </div>
  )
}
