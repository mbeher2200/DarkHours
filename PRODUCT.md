# Product

## Register

product

## Users

Serious astrophotographers planning specific shooting sessions. They own a telescope and tracking mount; hauling gear out means committing hours to a dark location. When they open this tool, they are answering one or two concrete questions: is tonight worth going, and if not, when is the next good night? They are data-literate and skeptical of consumer-facing oversimplification. They want the actual numbers and the reasoning behind the score, not a thumbs up or emoji verdict.

## Product Purpose

PyNightSkyPredictor gives astrophotographers the information they need to decide when and where to shoot. A single query returns a composite Night Quality Score (1–10) built from lunar interference (a Krisciunas & Schaefer 1991 × Winkler 2022 hybrid scattering model driven by live aerosol data), seeing and cloud cover (7Timer/GFS Cn² integration), clear dark sky hours, and Bortle-class light pollution. Beyond the score: per-target imaging windows clipped by moonlight interference with honest viability verdicts, nearby dark sky areas with drive times, a simulated 360° sky dome, horizon light-dome analysis, aurora and meteor-shower forecasts, satellite pass tables, and a 30-day outlook.

The product has two surfaces: the **DarkHours web app** (darkhours.app) — the primary, visual surface, including a red night-vision mode as a direct expression of design principle 2 — and the **CLI scripts** (`pynightsky.py`, `tripbuilder.py`), which add historical-weather scouting and multi-location trip comparison. The full validated feature list lives in `docs/FEATURES.md`.

Success means a user can look at the output and make a confident go/no-go decision — and trust that the numbers are grounded in real science, not a simplified index.

## Brand Personality

Ambitious · Polished · Inspiring

The tool should feel like a precision instrument built by someone who also cares about the craft. The interface should be worthy of the subject: the night sky is one of the most visually arresting things on earth, and the UI should carry some of that weight — not through decoration, but through intentional design quality. Every surface should feel considered, every data point should land with clarity.

## Anti-references

- **Generic SaaS dashboard** — flat grays, Tailwind defaults, interchangeable with any B2B analytics tool. This is not a productivity app; it should feel specific.
- **Consumer weather apps** — pastel gradients, cartoon sun/moon icons, oversimplified verdicts. The audience is technical; the interface should respect that.
- Cluttered legacy astronomy software (Stellarium-style information overload with no visual hierarchy) is also to be avoided, though it was not explicitly named — the output must be dense without feeling chaotic.

## Design Principles

1. **The data is the product.** Score, subscores, tables, windows, and timelines are what the user came for. The interface must deliver them with maximum clarity and minimum friction. No chrome that doesn't earn its place.
2. **Dark for a reason.** This tool is used at night, in planning for going outside into darkness. The dark theme is functionally correct — low-emission for dark-adapted eyes, contextually appropriate. It should feel deliberate, not default.
3. **Layered reading.** The score is the verdict; the breakdown is the argument; the raw data is the evidence. The visual hierarchy should express this: the headline number lands first, the subscores second, the detailed tables third. A user should be able to read at any depth and get value.
4. **Precision through restraint.** Polished and inspiring doesn't mean decorative. It means spacing that breathes exactly right, typography that scales with confidence, color that signals quality categories without shouting. The craft shows in what's absent as much as what's present.
5. **The sky is the reference palette.** Color, atmosphere, and texture should draw from the phenomena being modeled — astronomical twilight gradients, the blue-to-black of a clear dark sky, the amber of marginal conditions. Not neon sci-fi; not generic tech blues. Colors grounded in the actual visual experience of observing.

## Accessibility & Inclusion

No specific WCAG level required. All body text should read comfortably at typical viewing distances; semantic quality indicators (excellent / good / fair / poor) should be distinguishable without relying on color alone — the text labels must carry the meaning. Reduced motion should be respected.
