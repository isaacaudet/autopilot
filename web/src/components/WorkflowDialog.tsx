import { useMemo, useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Loader2, X } from 'lucide-react'
import { approveProcess, fetchConfig, fetchLayoutProfiles, fetchScore, type LayoutProfile } from '@/lib/api'
import type { ClipMeta, ConfigData, FetchScoreResponse, Tier } from '@/lib/types'
import { useChannelScope } from '@/hooks/useChannelScope'
import { CropProfilesDialog } from '@/components/CropProfilesDialog'

interface WorkflowDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  defaultRecipe?: string
}

const recipes = [
  { value: 'shorts', label: 'Batch Shorts', desc: 'Multiple shorts in parallel' },
  { value: 'compilation', label: 'Compilation', desc: 'Long-form compilation video' },
  { value: 'snipe', label: 'Daily Snipe', desc: 'Fetch top clips and process' },
]

const qualityColors: Record<string, string> = {
  excellent: 'text-green-400',
  good: 'text-yellow-400',
  decent: 'text-orange-400',
}

export function WorkflowDialog({ open, onOpenChange, defaultRecipe }: WorkflowDialogProps) {
  const navigate = useNavigate()
  const { channel: workspaceChannel, setChannel: setWorkspaceChannel, channels: workspaceChannels } = useChannelScope()
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [recipe, setRecipe] = useState(defaultRecipe ?? 'shorts')
  const [config, setConfig] = useState<ConfigData | null>(null)
  const [game, setGame] = useState('')
  const [fetchResult, setFetchResult] = useState<FetchScoreResponse | null>(null)
  const [fetchError, setFetchError] = useState('')
  const [selectedClipIds, setSelectedClipIds] = useState<string[]>([])
  const [excludedClipIds, setExcludedClipIds] = useState<string[]>([])
  const [clipSearch, setClipSearch] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [cropDialogOpen, setCropDialogOpen] = useState(false)
  const [layoutProfiles, setLayoutProfiles] = useState<Record<string, LayoutProfile> | null>(null)
  const [fetchWindow, setFetchWindow] = useState('24h')
  const [fetchScope, setFetchScope] = useState<'gamewide' | 'configured' | 'selected'>('gamewide')
  const [sourceSearch, setSourceSearch] = useState('')
  const [selectedSources, setSelectedSources] = useState<string[]>([])
  const [sourceAddInput, setSourceAddInput] = useState('')
  const [clipPanel, setClipPanel] = useState<'ranked' | 'fetched' | 'selected'>('ranked')
  const [reviewedClipIds, setReviewedClipIds] = useState<string[]>([])
  const [clipLayoutOverrides, setClipLayoutOverrides] = useState<Record<string, LayoutProfile>>({})
  const [shortsLayout, setShortsLayout] = useState<'blur' | 'fill'>(() => {
    try {
      const v = localStorage.getItem('clipper.shortsLayout') || 'blur'
      return v === 'fill' ? 'fill' : 'blur'
    } catch {
      return 'blur'
    }
  })

  useEffect(() => {
    if (open) {
      fetchConfig()
        .then((cfg) => setConfig(cfg))
        .catch(() => {})
      setStep(1)
      setFetchResult(null)
      setFetchError('')
      setSelectedClipIds([])
      setExcludedClipIds([])
      setClipSearch('')
      setLayoutProfiles(null)
      setFetchWindow('24h')
      setFetchScope('gamewide')
      setSourceSearch('')
      setSelectedSources([])
      setSourceAddInput('')
      setClipPanel('ranked')
      setReviewedClipIds([])
      setClipLayoutOverrides({})
      if (defaultRecipe) setRecipe(defaultRecipe)
    }
  }, [open, defaultRecipe])

  useEffect(() => {
    try {
      localStorage.setItem('clipper.shortsLayout', shortsLayout)
    } catch {
      // ignore storage failures
    }
  }, [shortsLayout])

  // Step 2: fetch and score, then auto-advance
  useEffect(() => {
    if (step !== 2 || !game) return
    let cancelled = false
    setFetchError('')
    setFetchResult(null)
    setExcludedClipIds([])
    setClipSearch('')
    setClipPanel('ranked')

    const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
    const sourceList = fetchScope === 'selected' ? selectedSources : []
    fetchScore(game, targetChannel, {
      period: fetchWindow,
      fetchScope,
      streamers: sourceList,
    })
      .then((result) => {
        if (cancelled) return
        setFetchResult(result)
        if (result.clip_count === 0) {
          setFetchError('No clips found for this game.')
        } else {
          setStep(3)
        }
      })
      .catch((err) => {
        if (!cancelled) setFetchError(err.message || 'Fetch failed')
      })

    return () => { cancelled = true }
  }, [step, game, workspaceChannel, fetchWindow, fetchScope, selectedSources])

  // Crop profiles for Fill portrait layout (facecam + HUD).
  useEffect(() => {
    if (!open) return
    if (step !== 3) return
    if (recipe !== 'shorts') return
    if (shortsLayout !== 'fill') return
    let cancelled = false
    fetchLayoutProfiles()
      .then((p) => { if (!cancelled) setLayoutProfiles(p) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [open, step, recipe, shortsLayout])

  function handleTierSelect(tier: Tier) {
    const ids = availableClips.slice(0, tier.count).map((c) => c.id)
    setSelectedClipIds(ids)
  }

  function handleCountSelect(count: number) {
    const ids = availableClips.slice(0, count).map((c) => c.id)
    setSelectedClipIds(ids)
  }

  async function handleApprove() {
    if (selectedClipIds.length === 0) return
    if (requiresPerClipReview && !allSelectedReviewed) {
      setFetchError('Review and confirm crop/layout for every selected clip before processing.')
      return
    }
    setSubmitting(true)
    try {
      const apiRecipe = recipe === 'snipe' ? 'compilation' : recipe
      const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
      const layout = recipe === 'shorts' ? shortsLayout : null
      const selectedOverrideMap = Object.fromEntries(
        Object.entries(clipLayoutOverrides).filter(([clipId]) => selectedClipIds.includes(clipId)),
      )
      if (requiresPerClipReview) {
        const missingOverrides = selectedClipIds.filter((clipId) => !selectedOverrideMap[clipId])
        if (missingOverrides.length > 0) {
          setFetchError('Some reviewed clips are missing crop overrides. Re-open Review Clips and confirm each one.')
          setSubmitting(false)
          return
        }
      }
      await approveProcess(selectedClipIds, apiRecipe, targetChannel, layout, selectedOverrideMap)
      onOpenChange(false)
      navigate('/')
    } catch {
      setFetchError('Failed to start processing')
    } finally {
      setSubmitting(false)
    }
  }

  const games = config?.targets.twitch.games ?? []
  const configuredStreamers = config?.targets.twitch.streamers ?? []
  const sourceCandidates = useMemo(() => {
    const uniq = new Set<string>()
    for (const s of configuredStreamers) {
      const v = String(s || '').trim()
      if (v) uniq.add(v)
    }
    for (const s of selectedSources) {
      const v = String(s || '').trim()
      if (v) uniq.add(v)
    }
    return Array.from(uniq)
  }, [configuredStreamers, selectedSources])

  const visibleStreamers = useMemo(() => {
    const q = sourceSearch.trim().toLowerCase()
    if (!q) return sourceCandidates
    return sourceCandidates.filter((s) => s.toLowerCase().includes(q))
  }, [sourceCandidates, sourceSearch])
  const isCompilation = recipe === 'compilation' || recipe === 'snipe'
  const channelEntries = Object.entries(workspaceChannels ?? {})
  const needsChannel = channelEntries.length > 0

  const excludedClipIdSet = useMemo(() => new Set(excludedClipIds), [excludedClipIds])
  const availableClips = useMemo(() => {
    if (!fetchResult) return []
    return fetchResult.clips.filter((c) => !excludedClipIdSet.has(c.id))
  }, [fetchResult, excludedClipIdSet])

  const selectedClips: ClipMeta[] = useMemo(() => {
    if (!fetchResult) return []
    const set = new Set(selectedClipIds)
    return availableClips.filter((c) => set.has(c.id))
  }, [fetchResult, availableClips, selectedClipIds])

  useEffect(() => {
    setReviewedClipIds((prev) => prev.filter((id) => selectedClipIds.includes(id)))
  }, [selectedClipIds])

  useEffect(() => {
    const selected = new Set(selectedClipIds)
    setClipLayoutOverrides((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([id]) => selected.has(id)))
      return next
    })
  }, [selectedClipIds])

  const requiresPerClipReview = recipe === 'shorts' && shortsLayout === 'fill' && selectedClipIds.length > 0
  const reviewedSet = useMemo(() => new Set(reviewedClipIds), [reviewedClipIds])
  const reviewedSelectedCount = useMemo(
    () => selectedClipIds.reduce((acc, id) => acc + (reviewedSet.has(id) ? 1 : 0), 0),
    [selectedClipIds, reviewedSet],
  )
  const allSelectedReviewed = !requiresPerClipReview || reviewedSelectedCount === selectedClipIds.length

  const visibleFetchedClips = useMemo(() => {
    if (!fetchResult) return []
    const q = clipSearch.trim().toLowerCase()
    const pool = fetchResult.clips
    if (!q) return pool
    return pool.filter((clip) => {
      const streamer = String(clip.streamer || '').toLowerCase()
      const title = String(clip.title || '').toLowerCase()
      return streamer.includes(q) || title.includes(q)
    })
  }, [fetchResult, clipSearch])

  useEffect(() => {
    if (!availableClips.length) {
      setSelectedClipIds([])
      return
    }
    const allowed = new Set(availableClips.map((c) => c.id))
    setSelectedClipIds((prev) => prev.filter((id) => allowed.has(id)))
  }, [availableClips])

  const missingCropStreamers = useMemo(() => {
    if (recipe !== 'shorts' || shortsLayout !== 'fill') return []
    if (selectedClips.length === 0) return []
    if (!layoutProfiles) return []

    const uniq = new Set<string>()
    for (const c of selectedClips) {
      const s = (c.streamer || '').trim()
      if (s) uniq.add(s)
    }

    const missing: string[] = []
    for (const s of uniq) {
      const key = s.toLowerCase()
      const prof = layoutProfiles[key]
      const needFacecam = prof?.facecam_enabled !== false
      const needHud = prof?.hud_enabled !== false
      if ((needFacecam && !prof?.facecam) || (needHud && !prof?.hud)) missing.push(s)
    }
    return missing.sort((a, b) => a.localeCompare(b))
  }, [recipe, shortsLayout, selectedClips, layoutProfiles])

  function addSource(source: string) {
    const candidate = String(source || '').trim()
    if (!candidate) return
    const lower = candidate.toLowerCase()
    setSelectedSources((prev) => {
      if (prev.some((s) => s.toLowerCase() === lower)) return prev
      return [...prev, candidate]
    })
  }

  const stepTitles: Record<number, string> = {
    1: 'Start Workflow',
    2: 'Fetching Clips...',
    3: isCompilation ? 'Pick Duration' : 'Pick Clip Count',
    4: 'Starting...',
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={step === 3 ? 'max-w-5xl' : step === 1 ? 'max-w-4xl' : 'max-w-md'}>
        <DialogHeader>
          <DialogTitle>{stepTitles[step]}</DialogTitle>
        </DialogHeader>

        {/* Step 1: Recipe + Game + Channel */}
        {step === 1 && (
          <div className="space-y-4 max-h-[70dvh] overflow-y-auto pr-1">
            <RadioGroup value={recipe} onValueChange={setRecipe} className="grid gap-2 sm:grid-cols-3">
              {recipes.map((r) => (
                <label
                  key={r.value}
                  className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-accent"
                >
                  <RadioGroupItem value={r.value} className="mt-0.5" />
                  <div>
                    <div className="font-medium">{r.label}</div>
                    <div className="text-sm text-muted-foreground">{r.desc}</div>
                  </div>
                </label>
              ))}
            </RadioGroup>

            <div className="grid gap-2">
              <label className="text-sm font-medium">Game</label>
              <Select value={game} onValueChange={setGame}>
                <SelectTrigger>
                  <SelectValue placeholder="Select game" />
                </SelectTrigger>
                <SelectContent>
                  {games.map((g) => (
                    <SelectItem key={g} value={g}>{g}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div className="grid gap-2">
                <label className="text-sm font-medium">Fetch Window</label>
                <Select value={fetchWindow} onValueChange={setFetchWindow}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="3h">Last 3 hours</SelectItem>
                    <SelectItem value="6h">Last 6 hours</SelectItem>
                    <SelectItem value="12h">Last 12 hours</SelectItem>
                    <SelectItem value="24h">Last 24 hours</SelectItem>
                    <SelectItem value="48h">Last 48 hours</SelectItem>
                    <SelectItem value="72h">Last 72 hours</SelectItem>
                    <SelectItem value="7d">Last 7 days</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="grid gap-2">
                <label className="text-sm font-medium">Clip Sources</label>
                <Select value={fetchScope} onValueChange={(v) => setFetchScope(v as 'gamewide' | 'configured' | 'selected')}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="gamewide">Game-wide (all Twitch channels)</SelectItem>
                    <SelectItem value="configured">Configured streamers</SelectItem>
                    <SelectItem value="selected">Pick streamers</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {fetchScope === 'configured' && (
              <div className="rounded-md border p-3 text-xs text-muted-foreground">
                Using {configuredStreamers.length} configured streamer{configuredStreamers.length === 1 ? '' : 's'} from config.
              </div>
            )}

            {fetchScope === 'selected' && (
              <div className="rounded-lg border p-3 space-y-3 bg-muted/10">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium">Select Streamers</div>
                  <div className="text-xs text-muted-foreground">
                    {selectedSources.length} selected
                  </div>
                </div>
                <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto_auto]">
                  <Input
                    value={sourceSearch}
                    onChange={(e) => setSourceSearch(e.target.value)}
                    placeholder="Search streamers..."
                    className="h-8"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setSelectedSources(configuredStreamers)}
                    disabled={configuredStreamers.length === 0}
                  >
                    All
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setSelectedSources(visibleStreamers)}
                    disabled={visibleStreamers.length === 0}
                  >
                    Search
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setSelectedSources([])}
                    disabled={selectedSources.length === 0}
                  >
                    Clear
                  </Button>
                </div>
                <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                  <Input
                    value={sourceAddInput}
                    onChange={(e) => setSourceAddInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        addSource(sourceAddInput)
                        setSourceAddInput('')
                      }
                    }}
                    placeholder="Add streamer login (e.g. eido)"
                    className="h-8"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      addSource(sourceAddInput)
                      setSourceAddInput('')
                    }}
                    disabled={!sourceAddInput.trim()}
                  >
                    Add
                  </Button>
                </div>
                {selectedSources.length > 0 && (
                  <div className="text-xs text-muted-foreground">
                    Active: {selectedSources.slice(0, 6).join(', ')}
                    {selectedSources.length > 6 ? ` +${selectedSources.length - 6} more` : ''}
                  </div>
                )}
                <div className="max-h-52 overflow-y-auto rounded-md border divide-y">
                  {visibleStreamers.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-muted-foreground">No matching streamers</div>
                  ) : (
                    [...visibleStreamers]
                      .sort((a, b) => {
                        const aSelected = selectedSources.includes(a) ? 0 : 1
                        const bSelected = selectedSources.includes(b) ? 0 : 1
                        if (aSelected !== bSelected) return aSelected - bSelected
                        return a.localeCompare(b)
                      })
                      .map((name) => {
                      const checked = selectedSources.includes(name)
                      return (
                        <label key={name} className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer hover:bg-accent/50">
                          <Checkbox
                            checked={checked}
                            onCheckedChange={(next) => {
                              setSelectedSources((prev) => {
                                if (next === true) {
                                  return prev.includes(name) ? prev : [...prev, name]
                                }
                                return prev.filter((s) => s !== name)
                              })
                            }}
                          />
                          <span className="truncate">{name}</span>
                          {checked && <span className="ml-auto text-[11px] text-primary">Selected</span>}
                        </label>
                      )
                    })
                  )}
                </div>
              </div>
            )}

            {needsChannel && (
              <div className="grid gap-2">
                <label className="text-sm font-medium">Target Channel</label>
                <Select
                  value={workspaceChannel}
                  onValueChange={setWorkspaceChannel}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Pick a channel" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Auto / no channel lock</SelectItem>
                    {channelEntries.map(([key, info]) => (
                      <SelectItem key={key} value={key}>
                        {info?.name ? info.name : key}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {recipe === 'shorts' && (
              <div className="grid gap-2">
                <label className="text-sm font-medium">Shorts Layout</label>
                <Select value={shortsLayout} onValueChange={(v) => setShortsLayout(v as 'blur' | 'fill')}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="blur">Classic (blur background)</SelectItem>
                    <SelectItem value="fill">Fill portrait (facecam + gameplay)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        )}

        {/* Step 2: Fetching spinner */}
        {step === 2 && (
          <div className="flex flex-col items-center gap-3 py-8">
            {!fetchError ? (
              <>
                <Loader2 className="size-8 animate-spin text-primary" />
                <p className="text-sm text-muted-foreground">
                  Fetching and scoring {game} clips...
                </p>
              </>
            ) : (
              <>
                <p className="text-sm text-destructive">{fetchError}</p>
                <Button variant="outline" onClick={() => setStep(1)}>Back</Button>
              </>
            )}
          </div>
        )}

        {/* Step 3: Configuration */}
        {step === 3 && fetchResult && (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {fetchResult.clip_count} clips scored and ranked
            </p>
            <p className="text-xs text-muted-foreground">
              Included: {availableClips.length} · Excluded: {excludedClipIds.length}
            </p>

            {isCompilation ? (
              <CompilationTierPicker
                tiers={fetchResult.tiers}
                clips={availableClips}
                selectedCount={selectedClipIds.length}
                onSelectTier={handleTierSelect}
              />
            ) : (
              <ShortCountPicker
                clips={availableClips}
                selectedCount={selectedClipIds.length}
                onSelectCount={handleCountSelect}
              />
            )}

            <div className="rounded-lg border bg-muted/5 overflow-hidden">
              <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
                <div className="inline-flex rounded-md border bg-background p-0.5">
                  <button
                    type="button"
                    className={`rounded px-2.5 py-1 text-xs ${clipPanel === 'ranked' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                    onClick={() => setClipPanel('ranked')}
                  >
                    Ranked
                  </button>
                  <button
                    type="button"
                    className={`rounded px-2.5 py-1 text-xs ${clipPanel === 'fetched' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                    onClick={() => setClipPanel('fetched')}
                  >
                    Fetched
                  </button>
                  <button
                    type="button"
                    className={`rounded px-2.5 py-1 text-xs ${clipPanel === 'selected' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                    onClick={() => setClipPanel('selected')}
                  >
                    Selected ({selectedClipIds.length})
                  </button>
                </div>

                {clipPanel === 'fetched' && (
                  <div className="ml-auto flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setExcludedClipIds([])}
                      disabled={excludedClipIds.length === 0}
                    >
                      Restore all
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setExcludedClipIds((prev) => {
                          const next = new Set(prev)
                          for (const clip of fetchResult.clips) {
                            if (!selectedClipIds.includes(clip.id)) next.add(clip.id)
                          }
                          return Array.from(next)
                        })
                      }}
                    >
                      Exclude non-selected
                    </Button>
                  </div>
                )}
              </div>

              <div className="space-y-2 p-3">
                {clipPanel === 'fetched' && (
                  <Input
                    value={clipSearch}
                    onChange={(e) => setClipSearch(e.target.value)}
                    placeholder="Filter fetched clips by streamer/title..."
                    className="h-8"
                  />
                )}

                {clipPanel === 'ranked' && (
                  <div className="rounded-md border divide-y max-h-72 overflow-auto">
                    {availableClips.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-muted-foreground">No included clips.</div>
                    ) : (
                      availableClips.slice(0, 30).map((clip, i) => (
                        <div key={clip.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                          <span className="font-mono text-muted-foreground w-4">{i + 1}</span>
                          <span className="text-primary font-medium truncate max-w-28">{clip.streamer}</span>
                          <span className="truncate flex-1">{clip.title}</span>
                          <span className="font-mono text-xs text-muted-foreground shrink-0">
                            {clip._score?.toFixed(0) ?? '-'} pts
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {clipPanel === 'fetched' && (
                  <div className="rounded-md border divide-y max-h-72 overflow-auto">
                    {visibleFetchedClips.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-muted-foreground">No clips match this filter.</div>
                    ) : (
                      visibleFetchedClips.map((clip) => {
                        const excluded = excludedClipIdSet.has(clip.id)
                        return (
                          <div
                            key={clip.id}
                            className={`flex items-center gap-3 px-3 py-2 text-sm ${excluded ? 'opacity-60' : ''}`}
                          >
                            <span className="text-primary font-medium truncate max-w-28">{clip.streamer}</span>
                            <span className="truncate flex-1">{clip.title}</span>
                            <span className="font-mono text-xs text-muted-foreground shrink-0">
                              {clip._score?.toFixed(0) ?? '-'} pts
                            </span>
                            <Button
                              size="sm"
                              variant={excluded ? 'outline' : 'ghost'}
                              onClick={() => {
                                setExcludedClipIds((prev) => {
                                  if (excluded) return prev.filter((id) => id !== clip.id)
                                  return prev.includes(clip.id) ? prev : [...prev, clip.id]
                                })
                              }}
                            >
                              {excluded ? 'Restore' : 'Exclude'}
                            </Button>
                          </div>
                        )
                      })
                    )}
                  </div>
                )}

                {clipPanel === 'selected' && (
                  <>
                    <div className="rounded-md border divide-y max-h-72 overflow-auto">
                      {selectedClips.length === 0 ? (
                        <div className="px-3 py-2 text-xs text-muted-foreground">No clips selected yet.</div>
                      ) : (
                        selectedClips.map((clip) => (
                          <div key={clip.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                            <span className="text-primary font-medium truncate max-w-28">{clip.streamer}</span>
                            <span className="truncate flex-1">{clip.title}</span>
                            <span className="font-mono text-xs text-muted-foreground shrink-0">
                              {clip._score?.toFixed(0) ?? '-'} pts
                            </span>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-8"
                              title="Remove from batch"
                              onClick={() => setSelectedClipIds((ids) => ids.filter((id) => id !== clip.id))}
                            >
                              <X className="size-4" />
                            </Button>
                          </div>
                        ))
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      Tip: remove low-confidence picks here before processing.
                    </div>
                  </>
                )}
              </div>
            </div>

            {recipe === 'shorts' && shortsLayout === 'fill' && selectedClipIds.length > 0 && (
              <div className="rounded-lg border p-3 space-y-2">
                <div className="text-xs uppercase tracking-wider text-muted-foreground">
                  Crop Calibration
                </div>
                {!layoutProfiles ? (
                  <div className="text-sm text-muted-foreground">Loading crop profiles…</div>
                ) : missingCropStreamers.length > 0 ? (
                  <>
                    <div className="text-sm">
                      No saved default profile yet for:{' '}
                      <span className="text-muted-foreground">
                        {missingCropStreamers.join(', ')}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-xs text-muted-foreground">
                        You can still process after clip-by-clip review. Saving a default profile here is optional.
                      </div>
                      <Button size="sm" variant="outline" onClick={() => setCropDialogOpen(true)}>
                        Set Crops
                      </Button>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="text-sm text-muted-foreground">
                      Reviewed {reviewedSelectedCount}/{selectedClipIds.length} selected clips.
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-xs text-muted-foreground">
                        Confirm each clip once. Last crop positions carry forward as you move clip-to-clip.
                      </div>
                      <Button size="sm" variant="outline" onClick={() => setCropDialogOpen(true)}>
                        Review Clips
                      </Button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {step === 1 && (
            <Button
              onClick={() => setStep(2)}
              disabled={!game || (fetchScope === 'selected' && selectedSources.length === 0)}
            >
              Fetch Clips
            </Button>
          )}
          {step === 3 && (
            <>
              <Button variant="outline" onClick={() => setStep(1)}>Back</Button>
              <Button
                onClick={handleApprove}
                disabled={submitting || selectedClipIds.length === 0 || !allSelectedReviewed}
              >
                {submitting ? (
                  <><Loader2 className="size-4 animate-spin mr-1" />Starting...</>
                ) : (
                  allSelectedReviewed
                    ? `Process ${selectedClipIds.length} Clips`
                    : `Review ${selectedClipIds.length - reviewedSelectedCount} More Clip${selectedClipIds.length - reviewedSelectedCount === 1 ? '' : 's'}`
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>

      <CropProfilesDialog
        open={cropDialogOpen}
        onOpenChange={(o) => {
          setCropDialogOpen(o)
          if (!o) {
            fetchLayoutProfiles()
              .then((p) => setLayoutProfiles(p))
              .catch(() => {})
          }
        }}
        clips={selectedClips}
        initialStreamer={missingCropStreamers[0] ?? null}
        reviewRequiredClipIds={selectedClipIds}
        confirmedClipIds={reviewedClipIds}
        onConfirmedClipIdsChange={setReviewedClipIds}
        clipOverrides={clipLayoutOverrides}
        onClipOverridesChange={setClipLayoutOverrides}
      />
    </Dialog>
  )
}

// ─── Sub-components ──────────────────────────────────────────────

function CompilationTierPicker({
  tiers,
  clips,
  selectedCount,
  onSelectTier,
}: {
  tiers: Tier[]
  clips: ClipMeta[]
  selectedCount: number
  onSelectTier: (tier: Tier) => void
}) {
  if (tiers.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Not enough clips for any compilation tier.
      </p>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-2">
      {tiers.map((tier) => {
        const isSelected = selectedCount === tier.count
        const disabled = tier.count > clips.length
        return (
          <button
            key={tier.target_min}
            onClick={() => {
              if (!disabled) onSelectTier(tier)
            }}
            className={`rounded-md border p-3 text-left transition-colors ${
              disabled ? 'opacity-50 cursor-not-allowed' : 'hover:bg-accent'
            } ${
              isSelected ? 'border-primary bg-accent' : ''
            }`}
            disabled={disabled}
          >
            <div className="text-sm font-medium">{tier.label}</div>
            <div className="text-xs mt-1 space-y-0.5">
              <div>{tier.count} clips &middot; {tier.actual_min} min</div>
              {disabled && (
                <div className="text-muted-foreground">Needs {tier.count} included clips (currently {clips.length})</div>
              )}
              <div className="text-muted-foreground">
                Avg score: {tier.avg_score}
              </div>
              <div className={`text-xs font-medium capitalize ${qualityColors[tier.quality] ?? ''}`}>
                {tier.quality}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}

function ShortCountPicker({
  clips,
  selectedCount,
  onSelectCount,
}: {
  clips: ClipMeta[]
  selectedCount: number
  onSelectCount: (count: number) => void
}) {
  const options = [5, 10, 20].filter((n) => n <= clips.length)

  return (
    <div className="flex items-center gap-2">
      {options.map((n) => (
        <Button
          key={n}
          variant={selectedCount === n ? 'default' : 'outline'}
          size="sm"
          onClick={() => onSelectCount(n)}
        >
          {n} clips
        </Button>
      ))}
    </div>
  )
}
