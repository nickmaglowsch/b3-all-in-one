---
name: Quantax
description: A calibrated reading instrument for a Brazilian investor's book, its taxes, and its backtests.
colors:
  navy: "#1b2a4a"
  paper: "#fafaf8"
  panel: "#ffffff"
  ink: "#17181c"
  muted: "#5c616b"
  hairline: "#e4e2dc"
  axis: "#c3c2b7"
  gain: "#006300"
  loss: "#d03b3b"
  series-1: "#2a78d6"
  series-2: "#eb6834"
  series-3: "#1baf7a"
  series-4: "#eda100"
  series-5: "#e87ba4"
  series-6: "#008300"
  series-7: "#4a3aa7"
  series-8: "#e34948"
  diverge-pos: "#2a78d6"
  diverge-neg: "#d03b3b"
  diverge-mid: "#f0efec"
  status-pending: "#5c616b"
  status-processing: "#b5760a"
  status-done: "#0ca30c"
  status-error: "#d03b3b"
typography:
  display:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "clamp(2.25rem, 5vw, 3.75rem)"
    fontWeight: 800
    lineHeight: 1.05
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "clamp(1.875rem, 3vw, 2.25rem)"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 800
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  body:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "0.6875rem"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "0.14em"
  numeric:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
    fontFeature: "tnum 1"
rounded:
  xs: "4px"
  md: "6px"
  lg: "8px"
  xl: "12px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.navy}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "rgb(27 42 74 / 0.9)"
    textColor: "#ffffff"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "6px 12px"
  button-secondary-hover:
    backgroundColor: "rgb(228 226 220 / 0.4)"
  input-text:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "16px"
  card-header:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    padding: "12px 16px"
  table-cell:
    textColor: "{colors.ink}"
    typography: "{typography.numeric}"
    padding: "8px 12px"
  table-header-cell:
    textColor: "{colors.muted}"
    padding: "8px 12px"
  status-badge:
    rounded: "{rounded.full}"
    padding: "2px 8px"
  ticker-chip:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xs}"
    padding: "2px 6px"
  nav-item:
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  nav-item-active:
    backgroundColor: "rgb(27 42 74 / 0.06)"
    textColor: "{colors.navy}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
---

# Design System: Quantax

## Overview

**Creative North Star: "The Instrument Panel"**

This is a calibrated reading instrument, not a dashboard. Everything in it is built for someone who reads closely and expects the readout to be honest — the chart series were validated for adjacency in both light and dark mode before they were tokens, the axis colour is a separate token from the border colour because a baseline is not a divider, and `tnum` is a first-class utility because a number that jitters between rows is a broken instrument. The visual system's job is to be trustworthy at close range.

The register is quiet and warm. The page plane is Bench Paper (#fafaf8), not white; the dark mode is a deliberately brown near-black (#1f1d1a) rather than the blue-black most tools reach for. Panels sit a half-step above the page in plain white, separated by Etched Rule hairlines rather than shadow. Type is a three-way split with no overlap in duty: Archivo carries every heading at weight 800, IBM Plex Sans carries prose at 14px, and IBM Plex Mono carries every number, every eyebrow, and every ticker. Density is high — the default body size is 14px and tables run at 12px cell padding — but the spacing is even and the rules are thin, so it reads as composed rather than crowded.

Three worlds this is explicitly **not**. It is not the dark glassy fintech dashboard — neon on black, glassmorphism, purple-to-blue gradients, glowing cards. It is not the playful neobank — rounded-everything, illustrated mascots, big friendly gradients, confetti on success — which optimises for reassuring a novice, the opposite of who this is built for. And it is not the generic admin template: zebra-striped tables, a blue-500 primary, a drop shadow on every card, Inter for everything. What this system *does* share DNA with is the trading terminal: high density and monospaced figures are the point here, not a hazard to guard against. Depth is the one area left deliberately open: the app is currently flat, and that is recorded below as observation rather than law.

**Key Characteristics:**
- Warm paper ground (#fafaf8), never pure white, in a warm-brown dark mode (#1f1d1a)
- Hairline rules do all separation work — no zebra striping, no fills to group rows
- Every number is IBM Plex Mono with tabular figures; columns never shift
- One accent (Oscilloscope Navy) reserved for interaction, kept scarce
- Colour never carries meaning alone — always paired with an icon, a glyph, or a label
- Charts use a purpose-built 8-series palette validated for adjacent-pair distinctness in both modes

## Colors

A warm neutral ground with a single cool accent, plus two purpose-built data palettes that never mix with the interface palette.

### Primary
- **Oscilloscope Navy** (`{colors.navy}`): The only interface accent. It marks where you are (active sidebar item, active section-nav tab, focus border on inputs) and what you can do (primary button fill, the check glyphs in the paywall's feature list). It is also the wordmark's Q: the mark is not set beside "Quantax" but *is* its first letter, a stroked ring fitted to Archivo 800's cap height and stem, with "uantax" following in ink. In dark mode it lightens to #5a8fd9 to hold contrast against the warm ground — which means the accent is a whisper against ink in light mode (1.25:1) and an obvious blue in dark (2.98:1). It never fills a card, tints a heading, or colours a data series.

### Neutral
- **Bench Paper** (`{colors.paper}`): The page plane, and the fill inside inputs, secondary buttons, and ticker chips. Warm off-white, so a true-white panel reads as raised without any shadow. Dark mode: #1f1d1a.
- **Panel White** (`{colors.panel}`): Cards, the sidebar, chart surfaces, dialogs — every surface that sits above the page. Dark mode: #2a2622.
- **Instrument Ink** (`{colors.ink}`): Primary text, table cell values, headings. Also the modal scrim at 30% and the mobile drawer scrim at 25%. Dark mode: #f5f3f0.
- **Legend Grey** (`{colors.muted}`): Secondary text, table column headers, eyebrows, axis tick labels, chart legend keys, and the resting state of every nav item. Dark mode: #a6adb8.
- **Etched Rule** (`{colors.hairline}`): Every border, divider, and table rule. Row rules run at 70% opacity (`border-hairline/70`) so the header rule reads as heavier than the rows beneath it. Also the hover wash for secondary controls, at 40%. Dark mode: #3d3a36.
- **Baseline Grey** (`{colors.axis}`): Chart baselines and axis lines only. Deliberately a separate token from Etched Rule — a chart's zero line is a measurement, an interface border is a separation, and they should not drift into each other. Dark mode: #7a7a7a.

### Tertiary
Three data-only families. None of them ever appear on interface chrome.

- **Money and delta** — **Gain Green** (`{colors.gain}`) and **Loss Red** (`{colors.loss}`), lightened in dark mode to #5ec05e / #ff5252. Used on signed values and directional deltas only.
- **Categorical series** (`{colors.series-1}` … `{colors.series-8}`): blue, orange, aqua, yellow, magenta, green, violet, red — in that order. The order is normative: it was chosen so that adjacent pairs stay distinguishable, and each has a dark-mode counterpart. Assign by index, never by aesthetic preference.
- **Diverging** (`{colors.diverge-pos}` / `{colors.diverge-neg}` / `{colors.diverge-mid}`): blue ↔ red around a neutral warm-grey midpoint, used for rebalance drift where the sign and the magnitude both matter.
- **Status** (`{colors.status-pending}` / `-processing` / `-done` / `-error`): a fixed, never-themed family for job state. Amber is darkened to #b5760a specifically to clear 3:1 on white.

### Named Rules

**The Never-Colour-Alone Rule.** No state or direction is ever communicated by colour by itself. A status badge is always icon + label + colour. A percentage delta always leads with ▲ / ▼ / – before it turns green or red. This is enforced in `StatusBadge` and `PctDelta` and must survive into every new component that expresses state.

**The Sign-Carries-Meaning Rule.** Green and red appear only where the sign of a number is itself the information — P&L, deltas, harvest results. A plain balance, a quantity, or a market value stays Instrument Ink no matter how large or small. Colouring every number is how a ledger stops being readable.

**The Scarce Accent Rule.** Oscilloscope Navy marks where you are and what you can do, and almost nothing else. Keep it scarce — a new use should have to earn its place against the fact that navy currently appears on roughly four things per screen. If a new surface needs emphasis, reach for weight, size, or rule-strength before reaching for the accent.

**The Two-Palette Rule.** Interface colour and data colour are separate systems. Never chart in navy; never build a button from a series colour. When a chart needs a single-series colour, it takes `series-1`, not the brand.

## Typography

**Display Font:** Archivo (with system-ui, sans-serif) — weights 700 and 800 only
**Body Font:** IBM Plex Sans (with system-ui, sans-serif) — weights 400, 500, 600
**Label/Mono Font:** IBM Plex Mono (with ui-monospace, monospace) — weights 400, 500, 600

**Character:** Archivo at 800 is compressed and flat-sided; against IBM Plex's humanist warmth it reads as a machined header on a paper form. The pairing is deliberately unbalanced — headings are much heavier than anything under them — so hierarchy survives at the small sizes this product runs at. Plex Mono is not decorative here: it is doing structural work on every number in the app.

### Hierarchy
- **Display** (800, `clamp(2.25rem, 5vw, 3.75rem)`, 1.05, -0.025em): The landing hero headline, and nothing else. The only place type is allowed to be the loudest thing on screen.
- **Headline** (800, `clamp(1.875rem, 3vw, 2.25rem)`, 1.1, -0.025em): Landing section heads and pricing figures. Marketing surfaces only.
- **Title** (800, 1.5rem, 1.25, -0.025em): The `PageHeader` h1 on every app page. At 24px against 14px body, the size ratio is doing the hierarchy work that colour is not allowed to do.
- **Body** (400, 0.875rem, 1.5): The app's default. Note this is 14px, not 16px — a deliberate density choice for a product whose primary user is reading tables. Prose blocks on marketing and IRPF report surfaces may step up to 1rem; keep measure at 65–75ch when they do.
- **Label** (Mono 500, 0.6875rem, 0.14em, uppercase): Eyebrows above page titles and in the paywall. The wide tracking is the signature — 0.14em is the standard, 0.12em appears on a few tighter marketing labels.
- **Numeric** (Mono 400/500, 0.875rem, `tnum`): Every currency value, percentage, quantity, date, and ticker. Applied through the `.tnum` utility (`font-variant-numeric: tabular-nums` + `font-feature-settings: "tnum" 1`).

### Named Rules

**The Tabular Column Rule.** Any number that will ever sit above or below another number gets Plex Mono and `tnum`, and is right-aligned. This covers every currency cell, every percentage, every quantity, and the axis ticks. A number that shifts its column width between renders is a defect, not a style preference.

**The Mono Eyebrow Rule.** The small uppercase label above a title is always IBM Plex Mono at 11px, 500 weight, 0.14em tracking, in Legend Grey. It is never sans-serif, never bold, never coloured with the accent.

**The Single Display Face Rule.** Archivo appears at 700/800 and never below. If a heading needs to be lighter or smaller than Title, it stops being a heading and becomes Plex Sans at 600.

### pt-BR formatting

The product is Brazilian-Portuguese only, and number formatting is part of the visual system, not just the data layer. Currency renders as `R$ 1.234,56` (BRL, comma decimal, period thousands), percentages as `1,23%` from fractions with 1–2 decimals, dates as `dd/mm/aaaa`, and a missing value as an em dash `—`, never as "N/A" or an empty cell. All of this comes from `lib/format.ts`; do not hand-format numbers in components.

## Layout

A fixed 240px sidebar (`w-60`) on the left from the `md` breakpoint up, with content in a 1152px max-width column (`max-w-6xl`) centred in the remaining space. Below `md` the sidebar becomes an off-canvas drawer behind a sticky 56px top bar, with an ink scrim at 25%; the drawer closes on navigation and on Escape. The landing and login routes opt out of the shell entirely — they render full-bleed with no sidebar.

Content padding is 16px on mobile and 32px from `md` up. Pages open with a `PageHeader` and a 24px gap beneath it. The primary page object is a vertical stack of cards; long pages get a sticky `SectionNav` under the top bar, and their anchor targets carry `scroll-mt-28 md:scroll-mt-16` to clear it.

Density is set by the components, not by a global scale: card padding is 16px, card headers are 12px/16px, table cells are 8px/12px, and control padding runs 8–10px vertical. Marketing sections breathe far wider — 64px to 112px of vertical padding — which is the clearest signal that the two regimes are different.

**The Card-Stack Rule.** App pages are a single-column stack of `Card`s, not a masonry or a multi-column grid. Width comes from the container, not from side-by-side cards. Two cards may sit side by side only when their contents are genuinely a comparison.

## Elevation & Depth

The application is flat. Depth comes entirely from tonal layering and hairlines: Bench Paper is the page, Panel White is the surface above it, and an Etched Rule border marks the boundary. There are no shadows on cards, on the sidebar, on inputs, or on the paywall dialog — the dialog separates itself with an ink scrim at 30% instead.

One shadow exists in the codebase: the chart tooltip's `shadow-sm`. It is recorded here as observation, not as a sanctioned exception — the elevation question is deliberately left open, so future work may introduce depth where it earns its place rather than working around a prohibition. The landing page's floating hero card (`shadow-xl shadow-navy/5` plus a 6-second float) went away when that page was rebuilt as the engine cross-section; the page is now flat like the rest of the app.

## Shapes

Rectangles with small, consistent radii, and borders everywhere fills would otherwise be needed.

The radius vocabulary is narrow and its distribution is the point: `{rounded.md}` (6px) is the default for anything interactive — buttons, inputs, nav items, tooltips — and accounts for the large majority of rounded elements in the app. `{rounded.lg}` (8px) is for surfaces: cards, dialogs, empty states. `{rounded.xl}` (12px) appears only on landing cards. `{rounded.xs}` (4px) is reserved for ticker chips, whose smaller radius keeps them reading as data rather than as controls. `{rounded.full}` is for status pills and the small state dots.

Borders are the primary form-defining device. A 1px Etched Rule border defines every card, input, secondary button, chip, and tooltip; the app has essentially no borderless filled surfaces except the primary button and the active nav item. Empty states signal their emptiness with the same border made dashed — the one place the border style changes. Silhouettes stay rectangular throughout: no pills except status badges, no circles except state dots and legend keys, no clipping or angled edges anywhere.

## Components

**Character: precise and recessive.** The chrome is designed to be the frame, not the picture. Borders over fills, hairlines over heavy rules, muted labels over bold ones — every component is trying to get out of the way of the number it is holding.

### Buttons
- **Shape:** Small radius (6px), no shadow, no gradient.
- **Primary:** Navy fill, white text, 14px semibold, 10px/16px padding. `hover:bg-navy/90`, `transition-colors`, `disabled:opacity-60`.
- **Secondary:** Etched Rule border on Bench Paper, ink text, 14px medium, 6px/12px padding. Hover washes the background with `hairline/40`. Disabled goes to 40% with `cursor-not-allowed`.
- **Tertiary / micro:** Bordered on paper at 11–12px, used for inline row actions; navy text when the action is navigational, muted text shading to Loss Red on hover when the action is destructive.
- **Icon-only:** No border, no fill, 4–6px padding, muted resting, ink on hover. Always carries an `aria-label`.

### Inputs / Fields
- **Style:** Etched Rule border on Bench Paper, 6px radius, 8px/12px padding, 14px ink text, muted placeholder.
- **Focus:** `focus:border-navy focus:outline-none` — the border shifts to the accent, no glow and no ring.
- **Numeric inputs:** right-aligned with Plex Mono and `tnum`, matching the tables they feed.

### Cards / Containers
- **Corner style:** 8px radius.
- **Background:** Panel White on the Bench Paper page.
- **Border:** 1px Etched Rule. No shadow.
- **Header:** Optional, separated by a full-width hairline, 12px/16px padding, title at 14px semibold ink, actions right-aligned.
- **Internal padding:** 16px.

### Tables
- **Structure:** Full-width, `border-collapse`, 14px. Column headers are 11px semibold uppercase Plex **Sans** in Legend Grey with wide tracking — deliberately not the mono eyebrow treatment, which belongs to page headers.
- **Rules:** A full-strength hairline under the header, then `hairline/70` between rows, and no rule after the last row (`last:border-0`).
- **Alignment:** Text left, numbers right. Right-aligned cells automatically get Plex Mono + `tnum`.
- **The No-Zebra Rule.** Rows are never striped and never filled on hover-for-decoration. Separation is the job of the rules alone. Zebra striping in this system reads as a foreign object.

### Status Badge
Full-radius pill, 2px/8px padding, 12px medium. Colour comes from the fixed status family at full strength for the text and 10% for the background fill, always accompanied by a 13px icon and a Portuguese label (Pendente / Processando / Concluído / Erro). Never rendered as a bare coloured dot without its label.

### Ticker Chip
The smallest object in the system and one of its signatures: 4px radius, Etched Rule border on Bench Paper, 11px Plex Mono medium, uppercase, tight leading, 2px/6px padding. It reads as a data token rather than a control, which is exactly why its radius is smaller than everything else.

### Navigation
Sidebar items are 14px medium at 8px/12px padding with an 18px leading icon and a 6px radius. Resting state is Legend Grey; hover washes `hairline/40` and darkens the label to ink; the active item takes a navy tint at 6% with navy text and `aria-current="page"`. The section nav uses the identical treatment at 12px, sticky under the top bar. Active state is always signalled by both the tint and the text colour, never the tint alone.

### Chart Tooltip
Panel surface, hairline border, 6px radius, `shadow-sm`, 12px. The label line is 11px mono in Legend Grey; each row is a 8px round series swatch, the series name in Legend Grey, and the value right-aligned in mono `tnum` ink. This is the one component where a shadow is currently used inside the app.

### Empty State
Dashed Etched Rule border, 8px radius, Panel White, 24px/64px padding, centred. Optional muted icon, then a 14px medium ink line, then a 14px muted hint, then an optional action. **The copy says what to do next, not that there is nothing here** — "Importe um extrato para começar", never "Sem dados".

### Paywall Dialog
Native `<dialog>` so focus trapping, Escape, and the backdrop come from the platform. 448px max width, 8px radius, hairline border, Panel White, ink scrim at 30%, 24px padding. Structure is mono eyebrow → Archivo 800 title → muted tagline → navy-checked feature list → Archivo price with `tnum` → primary + secondary button pair. This is the single most brand-dense component in the app and the best reference for how the type stack is meant to stack.

## Do's and Don'ts

### Do:
- **Do** put every number in IBM Plex Mono with `.tnum` and right-align it in tables.
- **Do** pair colour with an icon, a glyph, or a label whenever it carries state or direction.
- **Do** separate with hairlines. Use `border-hairline` at full strength for structural boundaries and `hairline/70` for row rules.
- **Do** reach for the `Card` → `PageHeader` → `DataTable` stack when building a new app surface; the density and rhythm come free.
- **Do** use the series palette in its documented order (`series-1` through `series-8`) and assign by index.
- **Do** route all number and date rendering through `lib/format.ts` so pt-BR formatting stays consistent.
- **Do** keep the accent scarce — a new navy usage should be able to justify itself as "where you are" or "what you can do".
- **Do** write empty-state copy as the next action.

### Don't:
- **Don't** drift toward dark glassy fintech: no neon-on-black, no glassmorphism, no purple-to-blue gradients, no glowing cards.
- **Don't** drift toward the playful neobank: no mascots, no confetti, no rounded-everything, no big friendly gradients. The primary user is reading closely, not being reassured.
- **Don't** settle into the generic admin template: no zebra stripes, no blue-500 primary, no drop shadow on every card, no Inter-for-everything.
- **Don't** zebra-stripe tables or fill rows to group them.
- **Don't** colour a plain balance green or red. Only signed, directional values get Gain/Loss.
- **Don't** chart in navy or build interface chrome from a series colour — the two palettes stay separate.
- **Don't** use pure white (#ffffff) as a page background; the page plane is Bench Paper (#fafaf8) and panels only read as raised because of it.
- **Don't** introduce a fourth typeface, or use Archivo below weight 700.
- **Don't** replace the mono eyebrow with a sans-serif or accent-coloured label.
- **Don't** render a missing value as "N/A", "-", or an empty cell; it is an em dash `—`.
- **Don't** add a focus ring or glow to inputs; focus is a border shift to navy.
