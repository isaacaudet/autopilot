import { useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { toast } from 'sonner'
import {
  fetchLayoutProfiles,
  removeLayoutProfile,
  saveLayoutProfile,
  type FacecamRect,
  type LayoutProfile,
} from '@/lib/api'
import type { ClipMeta } from '@/lib/types'

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v))
}

function normalizeRect(r: FacecamRect): FacecamRect {
  const w = clamp(Number(r.w) || 0.05, 0.05, 1)
  const h = clamp(Number(r.h) || 0.05, 0.05, 1)
  const x = clamp(Number(r.x) || 0, 0, 1 - w)
  const y = clamp(Number(r.y) || 0, 0, 1 - h)
  return {
    x: Math.round(x * 10000) / 10000,
    y: Math.round(y * 10000) / 10000,
    w: Math.round(w * 10000) / 10000,
    h: Math.round(h * 10000) / 10000,
  }
}

type CropKey = 'facecam' | 'hud'

type DragState =
  | { type: 'move'; key: CropKey; startX: number; startY: number; startRect: FacecamRect }
  | { type: 'resize'; key: CropKey; handle: 'nw' | 'ne' | 'sw' | 'se'; startX: number; startY: number; startRect: FacecamRect }
  | null

type LayoutTuning = {
  safeTopRatio: number
  safeBottomRatio: number
  facecamBandRatio: number
  gameplayZoom: number
  gameplayZoomNoFacecam: number
  gameplayXBias: number
  gameplayYBias: number
  hudHeightRatio: number
  hudScale: number
  hudXRatio: number
  hudYRatio: number
  titleYRatio: number
  subtitleMarginRatio: number
}

function rectEquals(a: FacecamRect, b: FacecamRect): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h
}

function tuningEquals(a: LayoutTuning, b: LayoutTuning): boolean {
  return (
    a.safeTopRatio === b.safeTopRatio &&
    a.safeBottomRatio === b.safeBottomRatio &&
    a.facecamBandRatio === b.facecamBandRatio &&
    a.gameplayZoom === b.gameplayZoom &&
    a.gameplayZoomNoFacecam === b.gameplayZoomNoFacecam &&
    a.gameplayXBias === b.gameplayXBias &&
    a.gameplayYBias === b.gameplayYBias &&
    a.hudHeightRatio === b.hudHeightRatio &&
    a.hudScale === b.hudScale &&
    a.hudXRatio === b.hudXRatio &&
    a.hudYRatio === b.hudYRatio &&
    a.titleYRatio === b.titleYRatio &&
    a.subtitleMarginRatio === b.subtitleMarginRatio
  )
}

function stringArrayEquals(a: string[], b: string[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false
  }
  return true
}

function layoutProfileEquals(a?: LayoutProfile, b?: LayoutProfile): boolean {
  if (a === b) return true
  if (!a && !b) return true
  if (!a || !b) return false
  const faceA = a.facecam
  const faceB = b.facecam
  if (!!faceA !== !!faceB) return false
  if (faceA && faceB && !rectEquals(faceA, faceB)) return false
  const hudA = a.hud
  const hudB = b.hud
  if (!!hudA !== !!hudB) return false
  if (hudA && hudB && !rectEquals(hudA, hudB)) return false

  return (
    a.facecam_enabled === b.facecam_enabled &&
    a.hud_enabled === b.hud_enabled &&
    a.safe_top_ratio === b.safe_top_ratio &&
    a.safe_bottom_ratio === b.safe_bottom_ratio &&
    a.facecam_band_ratio === b.facecam_band_ratio &&
    a.gameplay_zoom === b.gameplay_zoom &&
    a.gameplay_zoom_no_facecam === b.gameplay_zoom_no_facecam &&
    a.gameplay_x_bias === b.gameplay_x_bias &&
    a.gameplay_y_bias === b.gameplay_y_bias &&
    a.hud_height_ratio === b.hud_height_ratio &&
    a.hud_scale === b.hud_scale &&
    a.hud_x_ratio === b.hud_x_ratio &&
    a.hud_y_ratio === b.hud_y_ratio &&
    a.title_y_ratio === b.title_y_ratio &&
    a.subtitle_margin_ratio === b.subtitle_margin_ratio
  )
}

function layoutOverridesEqual(a: Record<string, LayoutProfile>, b: Record<string, LayoutProfile>): boolean {
  if (a === b) return true
  const aKeys = Object.keys(a)
  const bKeys = Object.keys(b)
  if (aKeys.length !== bKeys.length) return false
  for (const key of aKeys) {
    if (!Object.prototype.hasOwnProperty.call(b, key)) return false
    if (!layoutProfileEquals(a[key], b[key])) return false
  }
  return true
}

type CropCarryForward = {
  facecam: FacecamRect
  hud: FacecamRect
  facecamEnabled: boolean
  hudEnabled: boolean
  tuning: LayoutTuning
}

const TUNING_BOUNDS = {
  safeTopRatio: [0, 0.2],
  safeBottomRatio: [0, 0.25],
  facecamBandRatio: [0.16, 0.5],
  gameplayZoom: [0.75, 1.6],
  gameplayZoomNoFacecam: [0.75, 1.7],
  gameplayXBias: [-1, 1],
  gameplayYBias: [-1, 1],
  hudHeightRatio: [0.05, 0.22],
  hudScale: [0.5, 2],
  hudXRatio: [0, 1],
  hudYRatio: [0, 1],
  titleYRatio: [0, 0.6],
  subtitleMarginRatio: [0.05, 0.45],
} as const

const DEFAULT_TUNING: LayoutTuning = {
  safeTopRatio: 0.08,
  safeBottomRatio: 0.13,
  facecamBandRatio: 0.2,
  gameplayZoom: 1.02,
  gameplayZoomNoFacecam: 1.12,
  gameplayXBias: 0,
  gameplayYBias: 0,
  hudHeightRatio: 0.08,
  hudScale: 1,
  hudXRatio: 0.5,
  hudYRatio: 0.88,
  titleYRatio: 0.03,
  subtitleMarginRatio: 450 / 1920,
}

function clampTuningValue(key: keyof LayoutTuning, value: number): number {
  const [lo, hi] = TUNING_BOUNDS[key]
  return clamp(Number.isFinite(value) ? value : DEFAULT_TUNING[key], lo, hi)
}

function normalizeTuning(tuning: Partial<LayoutTuning> | null | undefined): LayoutTuning {
  const merged: LayoutTuning = { ...DEFAULT_TUNING, ...(tuning || {}) }
  return {
    safeTopRatio: Math.round(clampTuningValue('safeTopRatio', merged.safeTopRatio) * 10000) / 10000,
    safeBottomRatio: Math.round(clampTuningValue('safeBottomRatio', merged.safeBottomRatio) * 10000) / 10000,
    facecamBandRatio: Math.round(clampTuningValue('facecamBandRatio', merged.facecamBandRatio) * 10000) / 10000,
    gameplayZoom: Math.round(clampTuningValue('gameplayZoom', merged.gameplayZoom) * 10000) / 10000,
    gameplayZoomNoFacecam: Math.round(clampTuningValue('gameplayZoomNoFacecam', merged.gameplayZoomNoFacecam) * 10000) / 10000,
    gameplayXBias: Math.round(clampTuningValue('gameplayXBias', merged.gameplayXBias) * 10000) / 10000,
    gameplayYBias: Math.round(clampTuningValue('gameplayYBias', merged.gameplayYBias) * 10000) / 10000,
    hudHeightRatio: Math.round(clampTuningValue('hudHeightRatio', merged.hudHeightRatio) * 10000) / 10000,
    hudScale: Math.round(clampTuningValue('hudScale', merged.hudScale) * 10000) / 10000,
    hudXRatio: Math.round(clampTuningValue('hudXRatio', merged.hudXRatio) * 10000) / 10000,
    hudYRatio: Math.round(clampTuningValue('hudYRatio', merged.hudYRatio) * 10000) / 10000,
    titleYRatio: Math.round(clampTuningValue('titleYRatio', merged.titleYRatio) * 10000) / 10000,
    subtitleMarginRatio: Math.round(clampTuningValue('subtitleMarginRatio', merged.subtitleMarginRatio) * 10000) / 10000,
  }
}

function drawCoverCrop(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  src: { x: number; y: number; w: number; h: number },
  dst: { x: number; y: number; w: number; h: number },
  opts?: { alignX?: 'left' | 'center' | 'right'; alignY?: 'top' | 'center' | 'bottom' },
) {
  const alignX = opts?.alignX ?? 'center'
  const alignY = opts?.alignY ?? 'center'

  const scale = Math.max(dst.w / src.w, dst.h / src.h)
  const sampleW = dst.w / scale
  const sampleH = dst.h / scale

  const ox =
    alignX === 'left' ? 0 : alignX === 'right' ? src.w - sampleW : (src.w - sampleW) / 2
  const oy =
    alignY === 'top' ? 0 : alignY === 'bottom' ? src.h - sampleH : (src.h - sampleH) / 2

  const sx = src.x + ox
  const sy = src.y + oy

  ctx.drawImage(img, sx, sy, sampleW, sampleH, dst.x, dst.y, dst.w, dst.h)
}

function VerticalPreview({
  imageSrc,
  facecam,
  hud,
  facecamEnabled,
  hudEnabled,
  tuning,
}: {
  imageSrc: string
  facecam: FacecamRect
  hud: FacecamRect
  facecamEnabled: boolean
  hudEnabled: boolean
  tuning: LayoutTuning
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [img, setImg] = useState<HTMLImageElement | null>(null)

  useEffect(() => {
    if (!imageSrc) return
    const i = new Image()
    i.onload = () => setImg(i)
    i.src = imageSrc
    return () => {
      // best-effort cleanup
      setImg(null)
    }
  }, [imageSrc])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const outW = canvas.width
    const outH = canvas.height

    ctx.clearRect(0, 0, outW, outH)
    ctx.fillStyle = '#000'
    ctx.fillRect(0, 0, outW, outH)

    const imgW = img.naturalWidth || img.width
    const imgH = img.naturalHeight || img.height

    // Background blur fills the entire 9:16 frame (even in Fill mode)
    ctx.save()
    ctx.filter = 'blur(26px)'
    drawCoverCrop(ctx, img, { x: 0, y: 0, w: imgW, h: imgH }, { x: 0, y: 0, w: outW, h: outH })
    ctx.restore()
    ctx.fillStyle = 'rgba(0,0,0,0.24)'
    ctx.fillRect(0, 0, outW, outH)

    // Safe regions: leave blurred background visible so platform UI doesn't cover content.
    const safeTop = Math.round(outH * tuning.safeTopRatio)
    const safeBottom = Math.round(outH * tuning.safeBottomRatio)
    const contentH = Math.max(1, outH - safeTop - safeBottom)

    const faceH = facecamEnabled ? Math.round(contentH * tuning.facecamBandRatio) : 0
    const gameH = contentH - faceH

    if (facecamEnabled) {
      // Facecam (top band) — crop then cover-fill to full width.
      const faceSrc = {
        x: facecam.x * imgW,
        y: facecam.y * imgH,
        w: facecam.w * imgW,
        h: facecam.h * imgH,
      }
      drawCoverCrop(
        ctx,
        img,
        faceSrc,
        { x: 0, y: safeTop, w: outW, h: faceH },
        // Match backend: center-crop inside the facecam tile.
        { alignX: 'center', alignY: 'center' },
      )
    }

    // Gameplay (bottom band) — emulate backend:
    // 1) Crop off a bit of the top (facecam cutout) when stacking facecam + gameplay
    // 2) Crop off a bit of the bottom (HUD slice)
    // 2) Cover-scale into the gameplay band with a mild zoom
    // 3) Anchor bottom-center (keep center plane visible)
    const hudCropPad = 0.01
    const hudBarMinY = 0.75
    const hudBarMinW = 0.18
    const hudBarMinAspect = 1.35
    const hudCutoutMinW = 0.3
    const derivedBottomCrop = 1 - Math.min(1, hud.y + hudCropPad)
    const hudAspect = hud.h > 1e-6 ? hud.w / hud.h : 0
    const hudLooksLikeBar =
      hudEnabled && hud.y >= hudBarMinY && hud.w >= hudBarMinW && hudAspect >= hudBarMinAspect && hud.x <= 0.7
    const hudCutoutEnabled = hudLooksLikeBar && hud.w >= hudCutoutMinW
    const effectiveBottomCrop =
      hudCutoutEnabled ? Math.max(0, Math.min(0.14, derivedBottomCrop)) : 0

    // Facecam cutout (remove original facecam from gameplay so it doesn't appear twice).
    // Only apply when the facecam is near the center; corner facecams are usually
    // removed by the center crop anyway, and a top cut would remove the top HUD row.
    let topCrop = 0
    if (facecamEnabled) {
      const pad = 0.02
      const maxStart = 0.45
      const maxEnd = 0.75
      const maxCrop = 0.36
      const minCenterX = 0.33
      const maxCenterX = 0.67
      const y2 = facecam.y + facecam.h
      const cx = facecam.x + facecam.w / 2
      if (facecam.y <= maxStart && y2 <= maxEnd && cx >= minCenterX && cx <= maxCenterX) {
        topCrop = Math.min(maxCrop, Math.max(0, y2 + pad))
      }
    }

    const gameSrc = {
      x: 0,
      y: imgH * topCrop,
      w: imgW,
      h: imgH * (1 - topCrop - effectiveBottomCrop),
    }
    const zoom = Math.max(0.1, facecamEnabled ? tuning.gameplayZoom : tuning.gameplayZoomNoFacecam)
    const panXNorm = (tuning.gameplayXBias + 1) / 2
    const defaultYBias = hudEnabled ? 0 : 1
    const panYBias = Number.isFinite(tuning.gameplayYBias) ? tuning.gameplayYBias : defaultYBias
    const panYNorm = (panYBias + 1) / 2
    const yOverscan = 1 + 0.18 * Math.min(1, Math.abs(panYBias))
    const baseZoom = Math.max(1, zoom)
    const targetW = outW * baseZoom
    const targetH = gameH * baseZoom * yOverscan
    const scale = Math.max(targetW / gameSrc.w, targetH / gameSrc.h)
    const drawW = Math.max(1, gameSrc.w * scale)
    const drawH = Math.max(1, gameSrc.h * scale)
    const drawX = (outW - drawW) * panXNorm
    const drawYInBand = (gameH - drawH) * panYNorm
    const drawY = safeTop + faceH + drawYInBand

    if (zoom >= 1) {
      ctx.save()
      ctx.beginPath()
      ctx.rect(0, safeTop + faceH, outW, gameH)
      ctx.clip()
      ctx.drawImage(img, gameSrc.x, gameSrc.y, gameSrc.w, gameSrc.h, drawX, drawY, drawW, drawH)
      ctx.restore()
    } else {
      const plane = document.createElement('canvas')
      plane.width = outW
      plane.height = gameH
      const pctx = plane.getContext('2d')
      if (pctx) {
        pctx.clearRect(0, 0, outW, gameH)
        pctx.drawImage(img, gameSrc.x, gameSrc.y, gameSrc.w, gameSrc.h, drawX, drawYInBand, drawW, drawH)
        const shrinkW = outW * zoom
        const shrinkH = gameH * zoom
        const shrinkX = (outW - shrinkW) * panXNorm
        const shrinkY = safeTop + faceH + (gameH - shrinkH) * panYNorm
        ctx.drawImage(plane, 0, 0, outW, gameH, shrinkX, shrinkY, shrinkW, shrinkH)
      }
    }

    if (hudEnabled) {
      // HUD overlay — crop, contain-scale into the hud box, overlay near bottom (centered).
      const hudBoxH = Math.round(outH * tuning.hudHeightRatio)
      const hudSrc = {
        x: hud.x * imgW,
        y: hud.y * imgH,
        w: hud.w * imgW,
        h: hud.h * imgH,
      }
      const contain = Math.min((outW * tuning.hudScale) / hudSrc.w, (hudBoxH * tuning.hudScale) / hudSrc.h)
      const hudW = hudSrc.w * contain
      const hudH = hudSrc.h * contain

      const hudX = Math.round((outW - hudW) * tuning.hudXRatio)
      const hudY = Math.round((outH - hudH) * tuning.hudYRatio)

      // subtle shadow for readability
      ctx.save()
      ctx.shadowColor = 'rgba(0,0,0,0.55)'
      ctx.shadowBlur = 12
      ctx.shadowOffsetX = 0
      ctx.shadowOffsetY = 6
      ctx.drawImage(img, hudSrc.x, hudSrc.y, hudSrc.w, hudSrc.h, hudX, hudY, hudW, hudH)
      ctx.restore()

      // thin border hinting the HUD tile (helps users reason about placement)
      ctx.save()
      ctx.strokeStyle = 'rgba(16,185,129,0.65)' // emerald
      ctx.lineWidth = 2
      ctx.strokeRect(hudX, hudY, hudW, hudH)
      ctx.restore()
    }

    // Title preview line in top safe band.
    const titleY = Math.round(outH * tuning.titleYRatio)
    const titleH = Math.round(outH * 0.06)
    ctx.save()
    ctx.fillStyle = 'rgba(0,0,0,0.58)'
    ctx.fillRect(24, titleY, outW - 48, titleH)
    ctx.strokeStyle = 'rgba(255,255,255,0.2)'
    ctx.strokeRect(24, titleY, outW - 48, titleH)
    ctx.fillStyle = 'rgba(255,255,255,0.9)'
    ctx.font = 'bold 22px sans-serif'
    ctx.textBaseline = 'middle'
    ctx.fillText('TITLE PREVIEW', 36, titleY + titleH / 2)
    ctx.restore()

    // Subtitle preview line. This should sit on gameplay (or upper safe-bottom).
    const subtitleY = Math.round(outH - outH * tuning.subtitleMarginRatio)
    const subtitleH = Math.round(outH * 0.05)
    const subtitleTop = Math.max(0, Math.min(outH - subtitleH - 4, subtitleY - subtitleH))
    ctx.save()
    ctx.fillStyle = 'rgba(0,0,0,0.65)'
    ctx.fillRect(24, subtitleTop, outW - 48, subtitleH)
    ctx.strokeStyle = 'rgba(250,204,21,0.75)'
    ctx.strokeRect(24, subtitleTop, outW - 48, subtitleH)
    ctx.fillStyle = 'rgba(255,255,255,0.92)'
    ctx.font = 'bold 20px sans-serif'
    ctx.textBaseline = 'middle'
    ctx.fillText('SUBTITLE PREVIEW', 36, subtitleTop + subtitleH / 2)
    ctx.restore()
  }, [img, facecam, hud, facecamEnabled, hudEnabled, tuning])

  return (
    <div className="rounded-xl border bg-muted/10 p-3">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-muted-foreground">Vertical Preview</div>
        <div className="text-[11px] text-muted-foreground">
          {facecamEnabled ? 'Facecam + gameplay + title/subtitle guides' : 'Gameplay-only + title/subtitle guides'}
        </div>
      </div>

      <div className="mt-3 flex justify-center">
        <div className="relative rounded-[38px] border bg-gradient-to-b from-neutral-950 to-neutral-900 p-3 shadow-[0_24px_80px_rgba(0,0,0,0.65)]">
          <div className="pointer-events-none absolute left-1/2 top-3 h-6 w-28 -translate-x-1/2 rounded-full border border-white/10 bg-black/70" />
          <canvas
            ref={canvasRef}
            width={360}
            height={640}
            className="rounded-[30px] border bg-black"
          />
        </div>
      </div>
    </div>
  )
}

function GameplayPlanePreview({
  imageSrc,
  facecam,
  hud,
  facecamEnabled,
  hudEnabled,
  tuning,
}: {
  imageSrc: string
  facecam: FacecamRect
  hud: FacecamRect
  facecamEnabled: boolean
  hudEnabled: boolean
  tuning: LayoutTuning
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [img, setImg] = useState<HTMLImageElement | null>(null)

  useEffect(() => {
    if (!imageSrc) return
    const i = new Image()
    i.onload = () => setImg(i)
    i.src = imageSrc
    return () => setImg(null)
  }, [imageSrc])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const outW = canvas.width
    const outH = canvas.height
    const imgW = img.naturalWidth || img.width
    const imgH = img.naturalHeight || img.height

    const hudCropPad = 0.01
    const hudBarMinY = 0.75
    const hudBarMinW = 0.18
    const hudBarMinAspect = 1.35
    const hudCutoutMinW = 0.3
    const derivedBottomCrop = 1 - Math.min(1, hud.y + hudCropPad)
    const hudAspect = hud.h > 1e-6 ? hud.w / hud.h : 0
    const hudLooksLikeBar =
      hudEnabled && hud.y >= hudBarMinY && hud.w >= hudBarMinW && hudAspect >= hudBarMinAspect && hud.x <= 0.7
    const hudCutoutEnabled = hudLooksLikeBar && hud.w >= hudCutoutMinW
    const effectiveBottomCrop = hudCutoutEnabled ? Math.max(0, Math.min(0.14, derivedBottomCrop)) : 0

    let topCrop = 0
    if (facecamEnabled) {
      const pad = 0.02
      const maxStart = 0.45
      const maxEnd = 0.75
      const maxCrop = 0.36
      const minCenterX = 0.33
      const maxCenterX = 0.67
      const y2 = facecam.y + facecam.h
      const cx = facecam.x + facecam.w / 2
      if (facecam.y <= maxStart && y2 <= maxEnd && cx >= minCenterX && cx <= maxCenterX) {
        topCrop = Math.min(maxCrop, Math.max(0, y2 + pad))
      }
    }

    const gameSrc = {
      x: 0,
      y: imgH * topCrop,
      w: imgW,
      h: imgH * Math.max(0.05, 1 - topCrop - effectiveBottomCrop),
    }
    const zoom = Math.max(0.1, facecamEnabled ? tuning.gameplayZoom : tuning.gameplayZoomNoFacecam)
    const panXNorm = (tuning.gameplayXBias + 1) / 2
    const defaultYBias = hudEnabled ? 0 : 1
    const panYBias = Number.isFinite(tuning.gameplayYBias) ? tuning.gameplayYBias : defaultYBias
    const panYNorm = (panYBias + 1) / 2
    const yOverscan = 1 + 0.18 * Math.min(1, Math.abs(panYBias))
    const baseZoom = Math.max(1, zoom)
    const targetW = outW * baseZoom
    const targetH = outH * baseZoom * yOverscan
    const scale = Math.max(targetW / gameSrc.w, targetH / gameSrc.h)
    const drawW = Math.max(1, gameSrc.w * scale)
    const drawH = Math.max(1, gameSrc.h * scale)
    const drawX = (outW - drawW) * panXNorm
    const drawY = (outH - drawH) * panYNorm

    ctx.clearRect(0, 0, outW, outH)
    ctx.fillStyle = '#05070b'
    ctx.fillRect(0, 0, outW, outH)
    if (zoom >= 1) {
      ctx.save()
      ctx.beginPath()
      ctx.rect(0, 0, outW, outH)
      ctx.clip()
      ctx.drawImage(img, gameSrc.x, gameSrc.y, gameSrc.w, gameSrc.h, drawX, drawY, drawW, drawH)
      ctx.restore()
    } else {
      const plane = document.createElement('canvas')
      plane.width = outW
      plane.height = outH
      const pctx = plane.getContext('2d')
      if (pctx) {
        pctx.clearRect(0, 0, outW, outH)
        pctx.drawImage(img, gameSrc.x, gameSrc.y, gameSrc.w, gameSrc.h, drawX, drawY, drawW, drawH)
        const shrinkW = outW * zoom
        const shrinkH = outH * zoom
        const shrinkX = (outW - shrinkW) * panXNorm
        const shrinkY = (outH - shrinkH) * panYNorm
        ctx.drawImage(plane, 0, 0, outW, outH, shrinkX, shrinkY, shrinkW, shrinkH)
      }
    }

    ctx.fillStyle = 'rgba(0,0,0,0.2)'
    ctx.fillRect(0, 0, outW, 30)
    ctx.fillStyle = 'rgba(255,255,255,0.9)'
    ctx.font = 'bold 13px sans-serif'
    ctx.fillText('Gameplay Crop (horizontal source)', 10, 20)
  }, [img, facecam, hud, facecamEnabled, hudEnabled, tuning])

  return (
    <div className="rounded-xl border bg-muted/10 p-3">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">Horizontal Gameplay Preview</div>
      <div className="mt-3 flex justify-center">
        <canvas ref={canvasRef} width={640} height={360} className="w-full max-w-[640px] rounded-lg border bg-black" />
      </div>
    </div>
  )
}

function MultiRectEditor({
  imageSrc,
  facecam,
  hud,
  facecamEnabled,
  hudEnabled,
  active,
  onActiveChange,
  onChange,
}: {
  imageSrc: string
  facecam: FacecamRect
  hud: FacecamRect
  facecamEnabled: boolean
  hudEnabled: boolean
  active: CropKey
  onActiveChange: (k: CropKey) => void
  onChange: (k: CropKey, r: FacecamRect) => void
}) {
  const imgRef = useRef<HTMLImageElement | null>(null)
  const [drag, setDrag] = useState<DragState>(null)
  const [imgBox, setImgBox] = useState<{ left: number; top: number; width: number; height: number } | null>(null)
  const [imgFailed, setImgFailed] = useState(false)

  const rectByKey: Record<CropKey, FacecamRect> = { facecam, hud }

  useEffect(() => {
    function update() {
      const img = imgRef.current
      if (!img) return
      const b = img.getBoundingClientRect()
      setImgBox({ left: b.left, top: b.top, width: b.width, height: b.height })
    }
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  useEffect(() => {
    setImgFailed(false)
  }, [imageSrc])

  function toNorm(dx: number, dy: number) {
    if (!imgBox) return { dx: 0, dy: 0 }
    return { dx: dx / imgBox.width, dy: dy / imgBox.height }
  }

  function startMove(e: ReactPointerEvent, key: CropKey) {
    e.preventDefault()
    onActiveChange(key)
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    setDrag({ type: 'move', key, startX: e.clientX, startY: e.clientY, startRect: rectByKey[key] })
  }

  function startResize(e: ReactPointerEvent, key: CropKey, handle: 'nw' | 'ne' | 'sw' | 'se') {
    e.preventDefault()
    e.stopPropagation()
    onActiveChange(key)
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    setDrag({ type: 'resize', key, handle, startX: e.clientX, startY: e.clientY, startRect: rectByKey[key] })
  }

  function onPointerMove(e: ReactPointerEvent) {
    if (!drag || !imgBox) return
    const delta = toNorm(e.clientX - drag.startX, e.clientY - drag.startY)
    const minW = 0.08
    const minH = 0.08

    if (drag.type === 'move') {
      const next = normalizeRect({
        ...drag.startRect,
        x: drag.startRect.x + delta.dx,
        y: drag.startRect.y + delta.dy,
      })
      onChange(drag.key, next)
      return
    }

    const s = drag.startRect
    let x = s.x
    let y = s.y
    let w = s.w
    let h = s.h

    if (drag.handle === 'se') {
      w = clamp(s.w + delta.dx, minW, 1 - s.x)
      h = clamp(s.h + delta.dy, minH, 1 - s.y)
    } else if (drag.handle === 'sw') {
      x = clamp(s.x + delta.dx, 0, s.x + s.w - minW)
      w = clamp(s.w - delta.dx, minW, 1 - x)
      h = clamp(s.h + delta.dy, minH, 1 - s.y)
    } else if (drag.handle === 'ne') {
      y = clamp(s.y + delta.dy, 0, s.y + s.h - minH)
      h = clamp(s.h - delta.dy, minH, 1 - y)
      w = clamp(s.w + delta.dx, minW, 1 - s.x)
    } else if (drag.handle === 'nw') {
      x = clamp(s.x + delta.dx, 0, s.x + s.w - minW)
      y = clamp(s.y + delta.dy, 0, s.y + s.h - minH)
      w = clamp(s.w - delta.dx, minW, 1 - x)
      h = clamp(s.h - delta.dy, minH, 1 - y)
    }

    onChange(drag.key, normalizeRect({ x, y, w, h }))
  }

  function stopDrag() {
    setDrag(null)
  }

  function overlayStyle(r: FacecamRect) {
    return {
      left: `${r.x * 100}%`,
      top: `${r.y * 100}%`,
      width: `${r.w * 100}%`,
      height: `${r.h * 100}%`,
    }
  }

  const faceBorder = active === 'facecam' ? 'border-red-500/90 bg-red-500/10' : 'border-red-500/60 bg-transparent'
  const hudBorder = active === 'hud' ? 'border-emerald-400/90 bg-emerald-400/10' : 'border-emerald-400/60 bg-transparent'

  return (
    <div className="space-y-3">
      <div className="rounded-lg border bg-muted/10 p-2.5">
        <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rounded-sm bg-red-500" />
            Facecam
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rounded-sm bg-emerald-400" />
            HUD / Items
          </span>
        </div>
      </div>

      <div
        className="relative w-full select-none"
        onPointerMove={onPointerMove}
        onPointerUp={stopDrag}
        onPointerCancel={stopDrag}
      >
        <img
          ref={imgRef}
          src={imageSrc}
          alt=""
          className="w-full h-auto max-h-[calc(100dvh-460px)] rounded-md border bg-black"
          onLoad={() => {
            const img = imgRef.current
            if (!img) return
            const b = img.getBoundingClientRect()
            setImgBox({ left: b.left, top: b.top, width: b.width, height: b.height })
          }}
          onError={() => setImgFailed(true)}
        />

        {imgFailed && (
          <div className="absolute inset-0 flex items-center justify-center rounded-md border border-dashed bg-black/30 p-8 text-center">
            <div className="max-w-md space-y-2">
              <div className="text-sm font-medium">Thumbnail failed to load</div>
              <div className="text-xs text-muted-foreground">
                This usually means the backend couldn’t extract a frame (or the clip doesn’t exist locally yet).
                Try a different frame selector, or regenerate the clip output and retry.
              </div>
            </div>
          </div>
        )}

        {facecamEnabled && (
          <div
            className={`absolute border-2 ${faceBorder} ${active === 'facecam' ? 'cursor-move' : 'cursor-pointer'}`}
            style={overlayStyle(facecam)}
            onPointerDown={(e) => startMove(e, 'facecam')}
          >
            <div className="absolute left-2 top-2 rounded bg-black/60 px-2 py-1 text-[11px] text-white">
              Facecam
            </div>
            {active === 'facecam' && (
              <>
                <Corner handle="nw" onPointerDown={(e) => startResize(e, 'facecam', 'nw')} />
                <Corner handle="ne" onPointerDown={(e) => startResize(e, 'facecam', 'ne')} />
                <Corner handle="sw" onPointerDown={(e) => startResize(e, 'facecam', 'sw')} />
                <Corner handle="se" onPointerDown={(e) => startResize(e, 'facecam', 'se')} />
              </>
            )}
          </div>
        )}

        {hudEnabled && (
          <div
            className={`absolute border-2 ${hudBorder} ${active === 'hud' ? 'cursor-move' : 'cursor-pointer'}`}
            style={overlayStyle(hud)}
            onPointerDown={(e) => startMove(e, 'hud')}
          >
            <div className="absolute left-2 top-2 rounded bg-black/60 px-2 py-1 text-[11px] text-white">
              HUD
            </div>
            {active === 'hud' && (
              <>
                <Corner handle="nw" onPointerDown={(e) => startResize(e, 'hud', 'nw')} />
                <Corner handle="ne" onPointerDown={(e) => startResize(e, 'hud', 'ne')} />
                <Corner handle="sw" onPointerDown={(e) => startResize(e, 'hud', 'sw')} />
                <Corner handle="se" onPointerDown={(e) => startResize(e, 'hud', 'se')} />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Corner({
  handle,
  onPointerDown,
}: {
  handle: 'nw' | 'ne' | 'sw' | 'se'
  onPointerDown: (e: ReactPointerEvent) => void
}) {
  const pos =
    handle === 'nw'
      ? 'left-0 top-0 -translate-x-1/2 -translate-y-1/2 cursor-nwse-resize'
      : handle === 'ne'
        ? 'right-0 top-0 translate-x-1/2 -translate-y-1/2 cursor-nesw-resize'
        : handle === 'sw'
          ? 'left-0 bottom-0 -translate-x-1/2 translate-y-1/2 cursor-nesw-resize'
          : 'right-0 bottom-0 translate-x-1/2 translate-y-1/2 cursor-nwse-resize'
  return (
    <div
      className={`absolute ${pos} size-3 rounded-sm bg-white border border-black/30`}
      onPointerDown={onPointerDown}
    />
  )
}

// Defaults are "best guess" for Deadlock-style streams:
// - Facecam often top-left, not flush to the corner (HUD row is above it)
// - HUD/abilities are a wide bottom-left bar (health + abilities)
const DEFAULT_FACE: FacecamRect = { x: 0, y: 0.18, w: 0.3, h: 0.34 }
const DEFAULT_HUD: FacecamRect = { x: 0, y: 0.84, w: 0.58, h: 0.16 }

export function CropProfilesDialog({
  open,
  onOpenChange,
  clips,
  initialStreamer,
  reviewRequiredClipIds,
  confirmedClipIds,
  onConfirmedClipIdsChange,
  clipOverrides,
  onClipOverridesChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  clips: ClipMeta[]
  initialStreamer?: string | null
  reviewRequiredClipIds?: string[]
  confirmedClipIds?: string[]
  onConfirmedClipIdsChange?: (ids: string[]) => void
  clipOverrides?: Record<string, LayoutProfile>
  onClipOverridesChange?: (overrides: Record<string, LayoutProfile>) => void
}) {
  const [profiles, setProfiles] = useState<Record<string, LayoutProfile>>({})
  const [loading, setLoading] = useState(false)
  const [selectedStreamer, setSelectedStreamer] = useState<string>('')
  const [view, setView] = useState<'edit' | 'preview'>('edit')
  const [active, setActive] = useState<CropKey>('facecam')
  const [facecamRect, setFacecamRect] = useState<FacecamRect>(DEFAULT_FACE)
  const [hudRect, setHudRect] = useState<FacecamRect>(DEFAULT_HUD)
  const [facecamEnabled, setFacecamEnabled] = useState(true)
  const [hudEnabled, setHudEnabled] = useState(true)
  const [tuning, setTuning] = useState<LayoutTuning>(DEFAULT_TUNING)
  const [saving, setSaving] = useState(false)
  const [reviewIndex, setReviewIndex] = useState(0)
  const [localConfirmedClipIds, setLocalConfirmedClipIds] = useState<string[]>([])
  const [localClipOverrides, setLocalClipOverrides] = useState<Record<string, LayoutProfile>>({})
  const carryForwardRef = useRef<CropCarryForward | null>(null)

  const reviewClips = useMemo(() => {
    const all = clips.filter((c) => !!c.id)
    if (!reviewRequiredClipIds || reviewRequiredClipIds.length === 0) return all
    const byId = new Map(all.map((c) => [c.id, c]))
    return reviewRequiredClipIds
      .map((id) => byId.get(id))
      .filter((c): c is ClipMeta => !!c)
  }, [clips, reviewRequiredClipIds])

  const confirmedSet = useMemo(() => new Set(localConfirmedClipIds), [localConfirmedClipIds])
  const confirmedCount = useMemo(
    () => reviewClips.reduce((acc, c) => acc + (confirmedSet.has(c.id) ? 1 : 0), 0),
    [reviewClips, confirmedSet],
  )
  const currentReviewClip = reviewClips[Math.max(0, Math.min(reviewIndex, reviewClips.length - 1))] ?? null
  const isReviewMode = reviewClips.length > 0

  const streamers = useMemo(() => {
    const set = new Set<string>()
    for (const c of clips) if (c.streamer) set.add(c.streamer)
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [clips])
  const streamersKey = useMemo(() => streamers.join('|'), [streamers])

  const sampleClips = useMemo(() => {
    if (!selectedStreamer) return []
    return clips.filter((c) => c.streamer === selectedStreamer)
  }, [clips, selectedStreamer])

  const [sampleClipId, setSampleClipId] = useState<string | null>(null)
  const [thumbAt, setThumbAt] = useState<number | null>(0.35)

  useEffect(() => {
    if (!open) return
    if (isReviewMode) return
    if (!selectedStreamer) {
      setSampleClipId(null)
      return
    }
    if (sampleClips.length === 0) {
      setSampleClipId(null)
      return
    }
    setSampleClipId((prev) => (prev && sampleClips.some((c) => c.id === prev) ? prev : sampleClips[0].id))
  }, [open, selectedStreamer, sampleClips, isReviewMode])

  useEffect(() => {
    if (!open) return
    const incoming = (confirmedClipIds ?? []).filter((id) => reviewClips.some((c) => c.id === id))
    setLocalConfirmedClipIds((prev) => (stringArrayEquals(prev, incoming) ? prev : incoming))
  }, [open, confirmedClipIds, reviewClips])

  useEffect(() => {
    if (!open) return
    setLocalConfirmedClipIds((prev) => {
      const next = prev.filter((id) => reviewClips.some((c) => c.id === id))
      return stringArrayEquals(prev, next) ? prev : next
    })
  }, [open, reviewClips])

  useEffect(() => {
    if (!onConfirmedClipIdsChange) return
    const incoming = confirmedClipIds ?? []
    if (stringArrayEquals(incoming, localConfirmedClipIds)) return
    onConfirmedClipIdsChange(localConfirmedClipIds)
  }, [localConfirmedClipIds, confirmedClipIds, onConfirmedClipIdsChange])

  useEffect(() => {
    if (!open) return
    const source = clipOverrides ?? {}
    const next: Record<string, LayoutProfile> = {}
    for (const [clipId, value] of Object.entries(source)) {
      if (!value || typeof value !== 'object') continue
      next[clipId] = value
    }
    setLocalClipOverrides(next)
  }, [open])

  useEffect(() => {
    if (!open) return
    const allowed = new Set(clips.map((c) => c.id))
    setLocalClipOverrides((prev) => {
      const entries = Object.entries(prev).filter(([clipId]) => allowed.has(clipId))
      if (entries.length === Object.keys(prev).length) return prev
      const next = Object.fromEntries(entries)
      return next
    })
  }, [open, clips.map((c) => c.id).join('|')])

  useEffect(() => {
    if (!onClipOverridesChange) return
    const incoming = clipOverrides ?? {}
    if (layoutOverridesEqual(incoming, localClipOverrides)) return
    onClipOverridesChange(localClipOverrides)
  }, [localClipOverrides, clipOverrides, onClipOverridesChange])

  useEffect(() => {
    if (!open) return
    if (reviewClips.length === 0) {
      setReviewIndex(0)
      return
    }
    setReviewIndex((prev) => Math.max(0, Math.min(prev, reviewClips.length - 1)))
  }, [open, reviewClips])

  useEffect(() => {
    if (!open) return
    if (!isReviewMode) return
    if (!currentReviewClip) return
    if (currentReviewClip.streamer && currentReviewClip.streamer !== selectedStreamer) {
      setSelectedStreamer(currentReviewClip.streamer)
    }
    if (currentReviewClip.id !== sampleClipId) {
      setSampleClipId(currentReviewClip.id)
    }
  }, [open, currentReviewClip, selectedStreamer, sampleClipId, isReviewMode])

  useEffect(() => {
    if (!open) return
    if (!isReviewMode) return
    if (!sampleClipId) return
    const idx = reviewClips.findIndex((c) => c.id === sampleClipId)
    if (idx >= 0 && idx !== reviewIndex) {
      setReviewIndex(idx)
    }
  }, [open, sampleClipId, reviewClips, reviewIndex, isReviewMode])

  const activeClipId = isReviewMode ? (currentReviewClip?.id ?? sampleClipId) : sampleClipId

  const imageSrc = activeClipId
    ? `/api/clips/${encodeURIComponent(activeClipId)}/thumbnail?source=true${thumbAt === null ? '' : `&at=${thumbAt}`}`
    : ''

  useEffect(() => {
    if (!open) return
    setLoading(true)
    fetchLayoutProfiles()
      .then((p) => setProfiles(p))
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Failed to load crop profiles'))
      .finally(() => setLoading(false))
  }, [open])

  useEffect(() => {
    if (!open) return
    if (!selectedStreamer) {
      const preferred = (initialStreamer || '').trim()
      if (preferred && streamers.includes(preferred)) {
        setSelectedStreamer(preferred)
      } else if (streamers.length > 0) {
        setSelectedStreamer(streamers[0])
      }
      return
    }

    setView('edit')
    const key = selectedStreamer.trim().toLowerCase()
    const existing = profiles?.[key] ?? null
    const clipOverride = activeClipId ? localClipOverrides[activeClipId] : undefined

    const carry = carryForwardRef.current
    const fallbackFace = carry?.facecam ? normalizeRect(carry.facecam) : DEFAULT_FACE
    const fallbackHud = carry?.hud ? normalizeRect(carry.hud) : DEFAULT_HUD

    const face = clipOverride?.facecam
      ? normalizeRect(clipOverride.facecam)
      : existing?.facecam
        ? normalizeRect(existing.facecam)
        : fallbackFace
    const hud = clipOverride?.hud
      ? normalizeRect(clipOverride.hud)
      : existing?.hud
        ? normalizeRect(existing.hud)
        : fallbackHud
    const faceOn = clipOverride?.facecam_enabled != null
      ? clipOverride.facecam_enabled !== false
      : existing?.facecam_enabled === false
        ? false
        : (carry?.facecamEnabled ?? true)
    const hudOn = clipOverride?.hud_enabled != null
      ? clipOverride.hud_enabled !== false
      : existing?.hud_enabled === false
        ? false
        : (carry?.hudEnabled ?? true)
    const tuned = normalizeTuning({
      safeTopRatio: clipOverride?.safe_top_ratio ?? existing?.safe_top_ratio ?? carry?.tuning.safeTopRatio,
      safeBottomRatio: clipOverride?.safe_bottom_ratio ?? existing?.safe_bottom_ratio ?? carry?.tuning.safeBottomRatio,
      facecamBandRatio: clipOverride?.facecam_band_ratio ?? existing?.facecam_band_ratio ?? carry?.tuning.facecamBandRatio,
      gameplayZoom: clipOverride?.gameplay_zoom ?? existing?.gameplay_zoom ?? carry?.tuning.gameplayZoom,
      gameplayZoomNoFacecam: clipOverride?.gameplay_zoom_no_facecam ?? existing?.gameplay_zoom_no_facecam ?? carry?.tuning.gameplayZoomNoFacecam,
      gameplayXBias: clipOverride?.gameplay_x_bias ?? existing?.gameplay_x_bias ?? carry?.tuning.gameplayXBias,
      gameplayYBias: clipOverride?.gameplay_y_bias ?? existing?.gameplay_y_bias ?? carry?.tuning.gameplayYBias,
      hudHeightRatio: clipOverride?.hud_height_ratio ?? existing?.hud_height_ratio ?? carry?.tuning.hudHeightRatio,
      hudScale: clipOverride?.hud_scale ?? existing?.hud_scale ?? carry?.tuning.hudScale,
      hudXRatio: clipOverride?.hud_x_ratio ?? existing?.hud_x_ratio ?? carry?.tuning.hudXRatio,
      hudYRatio: clipOverride?.hud_y_ratio ?? existing?.hud_y_ratio ?? carry?.tuning.hudYRatio,
      titleYRatio: clipOverride?.title_y_ratio ?? existing?.title_y_ratio ?? carry?.tuning.titleYRatio,
      subtitleMarginRatio: clipOverride?.subtitle_margin_ratio ?? existing?.subtitle_margin_ratio ?? carry?.tuning.subtitleMarginRatio,
    })

    setFacecamRect((prev) => (rectEquals(prev, face) ? prev : face))
    setHudRect((prev) => (rectEquals(prev, hud) ? prev : hud))
    setFacecamEnabled((prev) => (prev === faceOn ? prev : faceOn))
    setHudEnabled((prev) => (prev === hudOn ? prev : hudOn))
    setTuning((prev) => (tuningEquals(prev, tuned) ? prev : tuned))
  }, [open, selectedStreamer, profiles, streamersKey, initialStreamer, currentReviewClip?.id, activeClipId, localClipOverrides])

  useEffect(() => {
    if (!open) return
    carryForwardRef.current = {
      facecam: normalizeRect(facecamRect),
      hud: normalizeRect(hudRect),
      facecamEnabled,
      hudEnabled,
      tuning: normalizeTuning(tuning),
    }
  }, [open, facecamRect, hudRect, facecamEnabled, hudEnabled, tuning])

  useEffect(() => {
    // Keep an enabled crop type selected.
    if (active === 'facecam' && !facecamEnabled && hudEnabled) setActive('hud')
    if (active === 'hud' && !hudEnabled && facecamEnabled) setActive('facecam')
  }, [active, facecamEnabled, hudEnabled])

  const hasProfile = useMemo(() => {
    const key = selectedStreamer.trim().toLowerCase()
    const p = profiles?.[key]
    return !!(p && (
      p.facecam ||
      p.hud ||
      p.facecam_enabled === false ||
      p.hud_enabled === false ||
      p.safe_top_ratio != null ||
      p.safe_bottom_ratio != null ||
      p.facecam_band_ratio != null ||
      p.gameplay_zoom != null ||
      p.gameplay_zoom_no_facecam != null ||
      p.gameplay_x_bias != null ||
      p.gameplay_y_bias != null ||
      p.hud_height_ratio != null ||
      p.hud_scale != null ||
      p.hud_x_ratio != null ||
      p.hud_y_ratio != null ||
      p.title_y_ratio != null ||
      p.subtitle_margin_ratio != null
    ))
  }, [profiles, selectedStreamer])

  const facecamCropZoom = useMemo(() => {
    const w = facecamRect.w > 1e-6 ? facecamRect.w : DEFAULT_FACE.w
    return Math.max(0.5, Math.min(2.5, DEFAULT_FACE.w / w))
  }, [facecamRect.w])

  function updateTuningField(key: keyof LayoutTuning, value: number) {
    setTuning((prev) => normalizeTuning({ ...prev, [key]: value }))
  }

  function updateFacecamField(field: keyof FacecamRect, value: number) {
    setFacecamRect((prev) => normalizeRect({ ...prev, [field]: value }))
  }

  function updateFacecamCropZoom(zoom: number) {
    const z = clamp(zoom, 0.5, 2.5)
    setFacecamRect((prev) => {
      const aspect = prev.h > 1e-6 ? prev.w / prev.h : (DEFAULT_FACE.w / DEFAULT_FACE.h)
      const targetW = clamp(DEFAULT_FACE.w / z, 0.08, 0.95)
      const targetH = clamp(targetW / aspect, 0.08, 0.95)
      const cx = prev.x + prev.w / 2
      const cy = prev.y + prev.h / 2
      return normalizeRect({
        x: cx - targetW / 2,
        y: cy - targetH / 2,
        w: targetW,
        h: targetH,
      })
    })
  }

  function buildCurrentLayoutProfile(): LayoutProfile {
    return {
      facecam: normalizeRect(facecamRect),
      hud: normalizeRect(hudRect),
      facecam_enabled: facecamEnabled,
      hud_enabled: hudEnabled,
      safe_top_ratio: tuning.safeTopRatio,
      safe_bottom_ratio: tuning.safeBottomRatio,
      facecam_band_ratio: tuning.facecamBandRatio,
      gameplay_zoom: tuning.gameplayZoom,
      gameplay_zoom_no_facecam: tuning.gameplayZoomNoFacecam,
      gameplay_x_bias: tuning.gameplayXBias,
      gameplay_y_bias: tuning.gameplayYBias,
      hud_height_ratio: tuning.hudHeightRatio,
      hud_scale: tuning.hudScale,
      hud_x_ratio: tuning.hudXRatio,
      hud_y_ratio: tuning.hudYRatio,
      title_y_ratio: tuning.titleYRatio,
      subtitle_margin_ratio: tuning.subtitleMarginRatio,
    }
  }

  function saveCurrentClipOverride(clipId: string | null | undefined) {
    if (!clipId) return
    const payload = buildCurrentLayoutProfile()
    setLocalClipOverrides((prev) => ({ ...prev, [clipId]: payload }))
  }

  async function handleSave(): Promise<boolean> {
    if (!selectedStreamer) return false
    setSaving(true)
    try {
      await saveLayoutProfile(selectedStreamer, buildCurrentLayoutProfile())
      toast.success('Saved crop profile')
      const next = await fetchLayoutProfiles()
      setProfiles(next)
      return true
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
      return false
    } finally {
      setSaving(false)
    }
  }

  function markCurrentClipConfirmed() {
    if (!currentReviewClip) return
    saveCurrentClipOverride(currentReviewClip.id)
    setLocalConfirmedClipIds((prev) => (prev.includes(currentReviewClip.id) ? prev : [...prev, currentReviewClip.id]))
  }

  async function handleSaveAndConfirm(next = false) {
    const ok = await handleSave()
    if (!ok) return
    markCurrentClipConfirmed()
    if (next && reviewClips.length > 0) {
      setReviewIndex((prev) => Math.min(prev + 1, reviewClips.length - 1))
    }
  }

  async function handleDelete() {
    if (!selectedStreamer) return
    setSaving(true)
    try {
      await removeLayoutProfile(selectedStreamer)
      toast.success('Removed profile')
      const next = await fetchLayoutProfiles()
      setProfiles(next)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="relative !p-0 !gap-0 !fixed !inset-3 !top-3 !left-3 !right-3 !bottom-3 !w-auto !h-auto !max-w-none sm:!max-w-none !translate-x-0 !translate-y-0 overflow-hidden rounded-xl lg:rounded-2xl">
        <div className="h-full grid grid-rows-[auto_minmax(0,1fr)]">
          <div className="border-b bg-gradient-to-b from-muted/20 to-transparent px-5 py-4 pr-14">
            <div className="flex items-center gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <DialogTitle className="text-base tracking-tight">Crops</DialogTitle>
                  <span className="rounded-full border bg-muted/10 px-2 py-0.5 text-[11px] text-muted-foreground">
                    Fill portrait
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Draw the facecam and HUD rectangles on the landscape frame, then preview the final 9:16.
                </p>
              </div>

              <div className="ml-auto flex items-center gap-3">
                <div className="hidden md:block text-[11px] text-muted-foreground">Streamer</div>
                <Select value={selectedStreamer} onValueChange={setSelectedStreamer} disabled={loading || streamers.length === 0}>
                  <SelectTrigger className="h-9 w-[260px]">
                    <SelectValue placeholder={streamers.length === 0 ? 'No clips loaded' : 'Select streamer'} />
                  </SelectTrigger>
                  <SelectContent>
                    {streamers.map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <div className="hidden sm:inline-flex rounded-full border bg-muted/10 p-1">
                  <button
                    className={`px-3 py-1 text-xs rounded-full transition-colors ${
                      view === 'edit' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
                    }`}
                    onClick={() => setView('edit')}
                    type="button"
                  >
                    Edit
                  </button>
                  <button
                    className={`px-3 py-1 text-xs rounded-full transition-colors ${
                      view === 'preview' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
                    }`}
                    onClick={() => setView('preview')}
                    type="button"
                    disabled={!activeClipId}
                    title={!activeClipId ? 'Pick a streamer with an available clip' : 'Preview the final vertical layout'}
                  >
                    Preview
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div className="grid h-full min-h-0 overflow-hidden md:grid-cols-[380px_1fr]">
            <div className="min-h-0 border-b md:border-b-0 md:border-r px-5 py-4 pb-36 overflow-y-auto">
              <div className="space-y-4">
            {!hasProfile && selectedStreamer && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
                <div className="text-xs font-medium text-amber-200">First-time setup</div>
                <p className="mt-1 text-xs text-muted-foreground">
                  This streamer doesn’t have a crop profile yet. Set the facecam and HUD crops once, then all future
                  “Fill portrait” renders will use it.
                </p>
              </div>
            )}

            <div className="sm:hidden inline-flex rounded-full border bg-muted/10 p-1 self-start">
              <button
                className={`px-3 py-1 text-xs rounded-full transition-colors ${
                  view === 'edit' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
                }`}
                onClick={() => setView('edit')}
                type="button"
              >
                Edit crops
              </button>
              <button
                className={`px-3 py-1 text-xs rounded-full transition-colors ${
                  view === 'preview' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
                }`}
                onClick={() => setView('preview')}
                type="button"
                disabled={!activeClipId}
                title={!activeClipId ? 'Pick a streamer with an available clip' : 'Preview the final vertical layout'}
              >
                Preview
              </button>
            </div>

            <div className="rounded-lg border p-3 space-y-2">
              <div className="text-xs uppercase tracking-wider text-muted-foreground">Crop Type</div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant={active === 'facecam' ? 'default' : 'outline'}
                  onClick={() => setActive('facecam')}
                  className="h-8"
                  disabled={!facecamEnabled}
                >
                  Facecam
                </Button>
                <Button
                  size="sm"
                  variant={active === 'hud' ? 'default' : 'outline'}
                  onClick={() => setActive('hud')}
                  className="h-8"
                  disabled={!hudEnabled}
                >
                  HUD / Items
                </Button>
              </div>
            </div>

            <div className="rounded-lg border p-3 space-y-3">
              <div className="text-xs uppercase tracking-wider text-muted-foreground">Presence</div>

              <label className="flex items-start gap-3">
                <Checkbox
                  checked={facecamEnabled}
                  onCheckedChange={(v) => setFacecamEnabled(v === true)}
                />
                <div className="space-y-0.5">
                  <div className="text-sm font-medium leading-none">Facecam present</div>
                  <p className="text-xs text-muted-foreground">
                    If off, this streamer will render as gameplay-only (zoomed) and still use the HUD overlay if set.
                  </p>
                </div>
              </label>

              <label className="flex items-start gap-3">
                <Checkbox
                  checked={hudEnabled}
                  onCheckedChange={(v) => setHudEnabled(v === true)}
                />
                <div className="space-y-0.5">
                  <div className="text-sm font-medium leading-none">HUD / items present</div>
                  <p className="text-xs text-muted-foreground">
                    If on, crop the HUD as a single rectangle that includes health + abilities (usually bottom-left).
                  </p>
                </div>
              </label>
            </div>

            <div className="rounded-lg border p-3 space-y-3">
              <div className="text-xs uppercase tracking-wider text-muted-foreground">Facecam, HUD & Text Position</div>
              <div className="grid gap-3 sm:grid-cols-2">
                <RangeField
                  label="Facecam X position"
                  value={facecamRect.x}
                  min={0}
                  max={1}
                  step={0.005}
                  onChange={(v) => updateFacecamField('x', v)}
                />
                <RangeField
                  label="Facecam Y position"
                  value={facecamRect.y}
                  min={0}
                  max={1}
                  step={0.005}
                  onChange={(v) => updateFacecamField('y', v)}
                />
                <RangeField
                  label="Facecam zoom"
                  value={facecamCropZoom}
                  min={0.5}
                  max={2.5}
                  step={0.01}
                  onChange={updateFacecamCropZoom}
                />
                <RangeField
                  label="Facecam band height"
                  value={tuning.facecamBandRatio}
                  min={0.16}
                  max={0.5}
                  step={0.01}
                  onChange={(v) => updateTuningField('facecamBandRatio', v)}
                  format={(v) => `${Math.round(v * 100)}%`}
                />
                <RangeField
                  label="HUD X position"
                  value={tuning.hudXRatio}
                  min={0}
                  max={1}
                  step={0.01}
                  onChange={(v) => updateTuningField('hudXRatio', v)}
                />
                <RangeField
                  label="HUD Y position"
                  value={tuning.hudYRatio}
                  min={0}
                  max={1}
                  step={0.01}
                  onChange={(v) => updateTuningField('hudYRatio', v)}
                />
                <RangeField
                  label="Title Y position"
                  value={tuning.titleYRatio}
                  min={0}
                  max={0.6}
                  step={0.005}
                  onChange={(v) => updateTuningField('titleYRatio', v)}
                  format={(v) => `${Math.round(v * 100)}%`}
                />
                <RangeField
                  label="Subtitle bottom margin"
                  value={tuning.subtitleMarginRatio}
                  min={0.05}
                  max={0.45}
                  step={0.005}
                  onChange={(v) => updateTuningField('subtitleMarginRatio', v)}
                  format={(v) => `${Math.round(v * 100)}%`}
                />
              </div>
            </div>
              </div>
            </div>

            <div className="px-5 py-4 pb-36 overflow-y-auto min-h-0">
              {!activeClipId ? (
                <div className="h-full flex items-center justify-center">
                  <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
                    Pick a streamer with an available clip in Studio to calibrate.
                  </div>
                </div>
              ) : (
                <div className="h-full flex flex-col gap-3 min-h-0">
                  {reviewClips.length > 0 && currentReviewClip && (
                    <div className="rounded-xl border bg-muted/10 px-3 py-2.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="text-xs uppercase tracking-wider text-muted-foreground">
                          Clip Review
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {reviewIndex + 1}/{reviewClips.length}
                        </div>
                        <div className="ml-auto text-xs text-muted-foreground">
                          Confirmed {confirmedCount}/{reviewClips.length}
                        </div>
                      </div>
                      <div className="mt-1 text-sm font-medium truncate">
                        {currentReviewClip.streamer || 'Unknown'} · {currentReviewClip.title || currentReviewClip.id}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setReviewIndex((i) => Math.max(0, i - 1))}
                          disabled={reviewIndex <= 0}
                        >
                          Previous
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setReviewIndex((i) => Math.min(reviewClips.length - 1, i + 1))}
                          disabled={reviewIndex >= reviewClips.length - 1}
                        >
                          Next
                        </Button>
                        <Button
                          size="sm"
                          variant={confirmedSet.has(currentReviewClip.id) ? 'secondary' : 'outline'}
                          onClick={markCurrentClipConfirmed}
                        >
                          {confirmedSet.has(currentReviewClip.id) ? 'Confirmed' : 'Confirm clip'}
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => { void handleSaveAndConfirm(reviewIndex < reviewClips.length - 1) }}
                          disabled={saving}
                        >
                          {reviewIndex < reviewClips.length - 1 ? 'Save + Confirm + Next' : 'Save + Confirm'}
                        </Button>
                      </div>
                    </div>
                  )}

                  {view === 'edit' ? (
                    <>
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="text-sm font-medium">Landscape (source frame)</div>
                          <div className="text-xs text-muted-foreground">
                            Draw both crops. Click a box to activate it, then resize.
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <div className="hidden lg:block text-[11px] text-muted-foreground">Sample clip</div>
                          <Select
                            value={thumbAt === null ? 'scene' : String(thumbAt)}
                            onValueChange={(v) => setThumbAt(v === 'scene' ? null : Number(v))}
                            disabled={!activeClipId}
                          >
                            <SelectTrigger className="h-8 w-[160px]">
                              <SelectValue placeholder="Frame" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="scene">Best (scene)</SelectItem>
                              <SelectItem value="0">First frame</SelectItem>
                              <SelectItem value="0.15">Early (15%)</SelectItem>
                              <SelectItem value="0.35">Mid (35%)</SelectItem>
                              <SelectItem value="0.6">Late (60%)</SelectItem>
                              <SelectItem value="0.8">Very late (80%)</SelectItem>
                            </SelectContent>
                          </Select>
                          <Select
                            value={activeClipId ?? ''}
                            onValueChange={(v) => setSampleClipId(v)}
                            disabled={sampleClips.length === 0}
                          >
                            <SelectTrigger className="h-8 w-[min(520px,42vw)]">
                              <SelectValue placeholder={sampleClips.length === 0 ? 'No clips' : 'Pick a clip frame'} />
                            </SelectTrigger>
                            <SelectContent>
                              {sampleClips.map((c) => (
                                <SelectItem key={c.id} value={c.id}>
                                  {(c.title || c.id).slice(0, 80)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>

                          <Button size="sm" variant="outline" onClick={() => setView('preview')} disabled={!activeClipId}>
                            Preview 9:16
                          </Button>
                        </div>
                      </div>
                      <div className="grid gap-3 2xl:grid-cols-[minmax(0,1fr)_380px]">
                        <div className="space-y-3">
                          <MultiRectEditor
                            imageSrc={imageSrc}
                            facecam={facecamRect}
                            hud={hudRect}
                            facecamEnabled={facecamEnabled}
                            hudEnabled={hudEnabled}
                            active={active}
                            onActiveChange={setActive}
                            onChange={(k, r) => (k === 'facecam' ? setFacecamRect(r) : setHudRect(r))}
                          />
                          <div className="rounded-xl border bg-muted/10 p-3 space-y-3">
                            <div className="text-xs uppercase tracking-wider text-muted-foreground">Placement & Zoom</div>
                            <div className="grid gap-3 md:grid-cols-2">
                              <RangeField
                                label="Main zoom (facecam)"
                                value={tuning.gameplayZoom}
                                min={0.75}
                                max={1.6}
                                step={0.01}
                                onChange={(v) => updateTuningField('gameplayZoom', v)}
                              />
                              <RangeField
                                label="Main zoom (no facecam)"
                                value={tuning.gameplayZoomNoFacecam}
                                min={0.75}
                                max={1.7}
                                step={0.01}
                                onChange={(v) => updateTuningField('gameplayZoomNoFacecam', v)}
                              />
                              <RangeField
                                label="Main X position"
                                value={tuning.gameplayXBias}
                                min={-1}
                                max={1}
                                step={0.01}
                                onChange={(v) => updateTuningField('gameplayXBias', v)}
                              />
                              <RangeField
                                label="Main Y position"
                                value={tuning.gameplayYBias}
                                min={-1}
                                max={1}
                                step={0.01}
                                onChange={(v) => updateTuningField('gameplayYBias', v)}
                              />
                              <RangeField
                                label="HUD zoom"
                                value={tuning.hudScale}
                                min={0.5}
                                max={2}
                                step={0.01}
                                onChange={(v) => updateTuningField('hudScale', v)}
                              />
                            </div>
                          </div>
                        </div>
                        <div className="space-y-3 2xl:sticky 2xl:top-0 self-start">
                          <VerticalPreview
                            imageSrc={imageSrc}
                            facecam={facecamRect}
                            hud={hudRect}
                            facecamEnabled={facecamEnabled}
                            hudEnabled={hudEnabled}
                            tuning={tuning}
                          />
                          <div className="rounded-xl border bg-muted/10 p-3 text-xs text-muted-foreground">
                            Live preview reflects your crop + zoom + title/subtitle height tuning while you edit.
                          </div>
                        </div>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="text-sm font-medium">Vertical preview</div>
                          <div className="text-xs text-muted-foreground">
                            Safe top for title, safe bottom for HUD. Facecam is stacked above gameplay when present.
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <div className="hidden lg:block text-[11px] text-muted-foreground">Sample clip</div>
                          <Select
                            value={thumbAt === null ? 'scene' : String(thumbAt)}
                            onValueChange={(v) => setThumbAt(v === 'scene' ? null : Number(v))}
                            disabled={!activeClipId}
                          >
                            <SelectTrigger className="h-8 w-[160px]">
                              <SelectValue placeholder="Frame" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="scene">Best (scene)</SelectItem>
                              <SelectItem value="0">First frame</SelectItem>
                              <SelectItem value="0.15">Early (15%)</SelectItem>
                              <SelectItem value="0.35">Mid (35%)</SelectItem>
                              <SelectItem value="0.6">Late (60%)</SelectItem>
                              <SelectItem value="0.8">Very late (80%)</SelectItem>
                            </SelectContent>
                          </Select>
                          <Select
                            value={activeClipId ?? ''}
                            onValueChange={(v) => setSampleClipId(v)}
                            disabled={sampleClips.length === 0}
                          >
                            <SelectTrigger className="h-8 w-[min(520px,42vw)]">
                              <SelectValue placeholder={sampleClips.length === 0 ? 'No clips' : 'Pick a clip frame'} />
                            </SelectTrigger>
                            <SelectContent>
                              {sampleClips.map((c) => (
                                <SelectItem key={c.id} value={c.id}>
                                  {(c.title || c.id).slice(0, 80)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>

                          <Button size="sm" variant="outline" onClick={() => setView('edit')}>
                            Back to edit
                          </Button>
                        </div>
                      </div>
                      <div className="grid gap-3 xl:grid-cols-[420px_1fr]">
                        <VerticalPreview
                          imageSrc={imageSrc}
                          facecam={facecamRect}
                          hud={hudRect}
                          facecamEnabled={facecamEnabled}
                          hudEnabled={hudEnabled}
                          tuning={tuning}
                        />
                        <GameplayPlanePreview
                          imageSrc={imageSrc}
                          facecam={facecamRect}
                          hud={hudRect}
                          facecamEnabled={facecamEnabled}
                          hudEnabled={hudEnabled}
                          tuning={tuning}
                        />
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

        </div>
        <div className="pointer-events-none absolute bottom-4 right-4 z-30">
          <div className="pointer-events-auto inline-flex items-center gap-2 rounded-full border bg-background/90 p-2 shadow-[0_16px_36px_rgba(0,0,0,0.45)] backdrop-blur">
            <Button size="sm" variant="outline" onClick={handleDelete} disabled={!selectedStreamer || saving}>
              Remove
            </Button>
            <Button size="sm" onClick={() => { void handleSave() }} disabled={!selectedStreamer || saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function RangeField({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  format?: (v: number) => string
}) {
  const pretty = format ? format(value) : value.toFixed(2)
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <div className="text-[11px] text-muted-foreground">{label}</div>
        <div className="text-[11px] font-mono text-muted-foreground">{pretty}</div>
      </div>
      <div className="grid grid-cols-[1fr_78px] gap-2">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={Number.isFinite(value) ? value : min}
          onChange={(e) => onChange(Number(e.target.value))}
          className="h-8"
        />
        <Input
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          value={Number.isFinite(value) ? value : min}
          onChange={(e) => onChange(Number(e.target.value))}
          className="h-8 font-mono text-xs"
        />
      </div>
    </div>
  )
}
