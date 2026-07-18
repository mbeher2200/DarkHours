// Loader for the Milky Way band texture (apps/web/public/mw.v1.png, generated
// by scripts/build_mw_texture.py from the ESO/S. Brunier all-sky panorama,
// CC BY 4.0). The texture is equirectangular in galactic coordinates:
// x → longitude l = 0..360° (wrapping), y → latitude b = +45° (top) .. −45°.
//
// Also builds the "grain" point cloud here: a deterministic importance sample
// of the texture used to render unresolved-starlight speckle at full canvas
// resolution (the band itself draws at low resolution and stays soft).

import { galToRaDec } from './astro'

/** Latitude coverage of the texture (degrees, symmetric about the plane). */
export const MW_B_MAX = 45

export interface MwTexture {
  w: number
  h: number
  /** RGBA rows from getImageData (stride 4). */
  data: Uint8ClampedArray
}

/**
 * Bilinear RGB sample at galactic (l, b) in degrees → [0..255]³.
 * Wraps in longitude; returns black outside |b| ≤ MW_B_MAX.
 */
export function sampleMw(tex: MwTexture, lDeg: number, bDeg: number): [number, number, number] {
  if (bDeg < -MW_B_MAX || bDeg > MW_B_MAX) return [0, 0, 0]
  const { w, h, data } = tex
  const x = ((lDeg / 360) * w + w) % w
  const y = Math.min(h - 1.001, Math.max(0, ((MW_B_MAX - bDeg) / (2 * MW_B_MAX)) * h - 0.5))
  const x0 = Math.floor(x - 0.5 + w) % w
  const x1 = (x0 + 1) % w
  const fx = ((x - 0.5 + w) % w) - Math.floor((x - 0.5 + w) % w)
  const y0 = Math.floor(y)
  const y1 = Math.min(h - 1, y0 + 1)
  const fy = y - y0
  const i00 = 4 * (y0 * w + x0), i10 = 4 * (y0 * w + x1)
  const i01 = 4 * (y1 * w + x0), i11 = 4 * (y1 * w + x1)
  const out: [number, number, number] = [0, 0, 0]
  for (let c = 0; c < 3; c++) {
    const top = data[i00 + c] * (1 - fx) + data[i10 + c] * fx
    const bot = data[i01 + c] * (1 - fx) + data[i11 + c] * fx
    out[c] = top * (1 - fy) + bot * fy
  }
  return out
}

// ── Grain point cloud ─────────────────────────────────────────────────────────

export interface GrainPoints {
  n: number
  raRad: Float32Array
  sinDec: Float32Array
  cosDec: Float32Array
  /** Relative brightness 0..1 (texture luma at the sampled texel). */
  w: Float32Array
}

/** Deterministic PRNG (mulberry32) so the grain field is stable across loads. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Importance-sample `count` sky positions with probability ∝ luma^1.5 × cos b
 * (the cos b corrects equirectangular texel solid angle), jittered within the
 * texel, precomputed to equatorial exactly like catalog stars.
 */
export function buildGrainPoints(tex: MwTexture, count = 8000): GrainPoints {
  const { w, h, data } = tex
  const cdf = new Float64Array(w * h)
  let total = 0
  for (let y = 0; y < h; y++) {
    const b = MW_B_MAX - ((y + 0.5) / h) * 2 * MW_B_MAX
    const cosB = Math.cos((b * Math.PI) / 180)
    for (let x = 0; x < w; x++) {
      const i = 4 * (y * w + x)
      const luma = (0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]) / 255
      total += Math.pow(luma, 1.5) * cosB
      cdf[y * w + x] = total
    }
  }

  const rand = mulberry32(0xc0ffee)
  const raRad = new Float32Array(count)
  const sinDec = new Float32Array(count)
  const cosDec = new Float32Array(count)
  const wArr = new Float32Array(count)
  for (let s = 0; s < count; s++) {
    const target = rand() * total
    // Binary search the cumulative distribution for the sampled texel.
    let lo = 0, hi = cdf.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (cdf[mid] < target) lo = mid + 1
      else hi = mid
    }
    const ty = Math.floor(lo / w)
    const tx = lo - ty * w
    const l = ((tx + rand()) / w) * 360
    const b = MW_B_MAX - ((ty + rand()) / h) * 2 * MW_B_MAX
    const { raDeg, decDeg } = galToRaDec(l, b)
    raRad[s] = (raDeg * Math.PI) / 180
    const dec = (decDeg * Math.PI) / 180
    sinDec[s] = Math.sin(dec)
    cosDec[s] = Math.cos(dec)
    const i = 4 * (ty * w + tx)
    wArr[s] = (0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]) / 255
  }
  return { n: count, raRad, sinDec, cosDec, w: wArr }
}

let texPromise: Promise<MwTexture> | null = null

/** Fetch + decode the band texture once per session (module-level cache). */
export function loadMwTexture(): Promise<MwTexture> {
  if (!texPromise) {
    texPromise = fetch(`${import.meta.env.BASE_URL}mw.v1.png`)
      .then(r => {
        if (!r.ok) throw new Error(`mw.v1.png: HTTP ${r.status}`)
        return r.blob()
      })
      .then(blob => createImageBitmap(blob))
      .then(bmp => {
        const c = document.createElement('canvas')
        c.width = bmp.width
        c.height = bmp.height
        const ctx = c.getContext('2d', { willReadFrequently: true })!
        ctx.drawImage(bmp, 0, 0)
        const img = ctx.getImageData(0, 0, bmp.width, bmp.height)
        bmp.close()
        return { w: img.width, h: img.height, data: img.data }
      })
      .catch(err => {
        texPromise = null // allow a retry on the next mount
        throw err
      })
  }
  return texPromise
}
