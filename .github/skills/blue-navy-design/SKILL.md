---
name: blue-navy-design
description: >-
  Restyle an existing web UI into the "blue navy" design system — deep navy
  chrome, a teal primary that lightens to cyan on hover, square corners, white
  cards with an accent top-border, Roboto/Open Sans type, and a short cyan
  underline beneath headings. Works as a token-first restyle that keeps layout
  and behavior intact.
  WHEN: "apply blue navy design", "restyle to navy theme", "navy and cyan
  redesign", "make the UI look corporate blue", "square corners restyle",
  "rebrand the frontend", "swap the design tokens", "apply our blue design
  system".
version: 1.0.0
author: michalmar
license: MIT
tags: [frontend, design-system, branding, theming, css, restyle]
---

# Blue Navy Design System

A corporate-institutional web aesthetic: a **deep navy chrome bar** over a light
blue-grey page wash, **white cards with an accent top-border**, **square
corners everywhere**, a **teal primary that lightens to cyan on hover**, and
**Roboto headings over Open Sans body copy**. Headings carry a short cyan
underline. Primary calls to action are prefixed with an arrow.

See `assets/` for the target look: `01-app-light.png`, `02-app-dark.png`,
`03-auth-card.png`.

This skill is written for a **restyle**, not a rebuild. Layout, component
structure, and behavior stay as they are; only the visual layer changes.

---

## Design tokens

### Light theme

| Role | Value | Applied to |
| --- | --- | --- |
| Chrome | `#00205B` | header / top bar / footer background |
| Chrome foreground | `#FFFFFF` | text and icons on chrome |
| Chrome muted | `#A2CCE3` | de-emphasised chrome detail |
| Page wash | `#F4F7FA` | app background |
| Surface | `#FFFFFF` | cards, panels, composer |
| Body text | `#12304D` | deep slate — **never pure black** |
| Muted text | `#5A7183` | secondary copy |
| Border | `#D8E2EA` | hairlines |
| Border strong | `#A2CCE3` | emphasised hairlines |
| **Primary** | `#007394` | buttons, links, active accents |
| **Primary hover** | `#00ADE9` | hover state — a *lighten to cyan*, not a darken |
| Primary foreground | `#FFFFFF` | text on primary |
| Primary soft | `#E6F4FA` | selected rows, subtle fills |
| Primary border | `#99DEF6` | soft accent hairline |
| Accent ring | `rgba(0, 173, 233, 0.18)` | focus glow |
| Focus ring | `rgba(0, 173, 233, 0.55)` | focus outline |
| Surface muted | `#EEF3F7` | hover fills, inset panels |
| Scrim | `rgba(0, 32, 91, 0.45)` | modal backdrop — navy-tinted, not grey |
| Card shadow | `0 2px 15px rgba(0, 32, 91, 0.10)` | the signature soft navy shadow |
| Button shadow | `0 4px 12px rgba(0, 115, 148, 0.20)` | |

### Dark theme

Re-key the palette off navy rather than grey. Keep the **chrome brighter than
the page background** or the top bar disappears into the body.

| Role | Value |
| --- | --- |
| Chrome | `#00205B` (stays brand navy) |
| Page wash | `#001233` |
| Surface | `#0A1F47` |
| Surface muted | `#0D2647` |
| Body text | `#E4EEF6` |
| Muted text | `#9BB4C9` |
| Border | `#123156` |
| Border strong | `#1E4A76` |
| Primary | `#00ADE9` |
| Primary hover | `#99DEF6` |
| Primary foreground | `#001233` |
| Primary soft | `#0D2B52` |

### Type

| Role | Value |
| --- | --- |
| Display / headings | **Roboto**, weight **700** |
| Body | **Open Sans**, weight 400 / 600 |
| Mono | keep whatever mono the project already uses, for IDs, telemetry and timestamps |
| Letter-spacing | **`0`** everywhere |
| Heading scale | page/hero 1.6–2rem · panel title ~1.05rem · sub-header ~0.95rem |

Load only the weights you use:
`Roboto:wght@400;500;700` and `Open+Sans:wght@400;600;700`.

### Shape and motion

| Property | Value |
| --- | --- |
| Border radius | **`0`** — square corners are the signature |
| Radius exceptions | circular avatars, status dots, and spinners stay `50%` |
| Transition | `300ms ease` on colour changes (slower and softer than typical UI) |
| Card border | `border-top: 4px solid` accent on standalone cards |
| Row selection | `border-left: 3px solid` cyan, not a filled pill |
| Heading underline | `60px × 3px` cyan bar under the heading |
| CTA prefix | `→` before primary button labels |

---

## Component recipes

### Chrome bar

```css
.app-header {
  min-height: 64px;
  padding: 0 12px;
  color: var(--chrome-fg);
  background: var(--chrome);
  /* no bottom border — the navy already separates it */
}

.app-header .icon-button { color: rgba(255, 255, 255, 0.72); }
.app-header .icon-button:hover {
  color: var(--chrome-fg);
  background: rgba(255, 255, 255, 0.14);
}
.app-header .icon-button:focus-visible { outline-color: var(--accent-hover); }

.header-separator { background: rgba(255, 255, 255, 0.24); }
```

### Brand mark

Three skewed vertical bars of unequal height — angular, flight-path-like. Give
it a light-surface variant and a chrome variant so it reads on both:

```css
.brand-mark {
  display: grid;
  grid-auto-flow: column;
  gap: 3px;
  align-content: center;
  justify-content: center;
  width: 22px;
  height: 22px;
}
.brand-mark span {
  width: 5px;
  height: 14px;
  background: var(--accent);
  transform: skewX(-18deg);
}
.brand-mark span:nth-child(2) { height: 20px; background: var(--accent-hover); }
.brand-mark span:nth-child(3) { height: 9px; }

/* on the navy bar */
.app-header .brand-mark span { background: var(--chrome-fg); }
.app-header .brand-mark span:nth-child(2) { background: var(--accent-hover); }
```

### Card

```css
.card {
  border: 1px solid var(--border);
  border-top: 4px solid var(--accent);
  border-radius: var(--radius);
  background: var(--card);
  box-shadow: var(--shadow-sm);
  transition: border-color 300ms ease;
}
```

For hoverable cards, start `border-top-color: transparent` and reveal the accent
(plus an accent title colour) on `:hover`.

### List row

```css
.row {
  border-left: 3px solid transparent;
  transition: background-color 100ms ease, border-color 160ms ease;
}
.row:hover  { border-left-color: var(--border-strong); background: var(--surface-muted); }
.row.active { border-left-color: var(--accent-hover); background: var(--accent-soft); }
```

### Buttons

```css
.primary-button {
  min-height: 36px;
  padding: 0 20px;
  border: 0;
  border-radius: var(--radius);
  color: var(--accent-fg);
  background: var(--accent);
  font-size: 0.78rem;
  font-weight: 600;
  gap: 6px;
  transition: background-color 300ms ease;
}
.primary-button:hover:not(:disabled) { background: var(--accent-hover); }

/* arrow prefix — skip buttons that already carry an icon */
.primary-button:not(:has(.icon))::before {
  content: '\2192';
  font-weight: 400;
}
```

In a JS tagged-template stylesheet (Lit `css`, styled-components), write the
escape as `'\\2192'` in source so the browser receives `'\2192'`.

### Heading underline

```css
h2 {
  position: relative;
  padding-bottom: 14px;
  font-family: var(--font-display);
  font-weight: 700;
}
h2::after {
  position: absolute;
  bottom: 0;
  left: 0;              /* left: 50%; transform: translateX(-50%) when centered */
  width: 60px;
  height: 3px;
  content: '';
  background: var(--accent-hover);
}
```

### Input / composer frame

```css
.field {
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--card);
  box-shadow: var(--shadow-sm);
  transition: border-color 300ms ease, box-shadow 300ms ease;
}
.field:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-ring);
}
```

---

## How to apply it

Work in phases and **screenshot after each one**. Phase 1 alone delivers most of
the visual shift.

### Phase 0 — Survey first

1. Find where design tokens live. If the project already centralises CSS custom
   properties in one `:root` block, the restyle is mostly a token edit.
   If tokens are scattered, introduce the token layer first and migrate to it —
   do not hand-edit colours in dozens of files.
2. Capture a baseline screenshot of the current UI to diff against.
3. Note which surfaces are chrome (bar, footer, nav) vs content (cards, rails).

### Phase 1 — Tokens

Swap the palette, fonts and shadows per the tables above. Add radius tokens so
square corners are a single switch:

```css
--radius: 0px;
--radius-sm: 0px;
--radius-pill: 0px;
```

Then **wire them into every hardcoded radius** in the codebase — otherwise the
tokens are dead code and no corners change. Keep `50%` for avatars, status dots
and spinners, and keep `inherit` where present.

```bash
# inventory before editing
grep -rhoE "border-radius: [^;]+;" src | sort | uniq -c | sort -rn
```

### Phase 2 — Chrome

Navy header/nav, white brand text, translucent-white icon buttons and
separators, cyan focus outlines. Accent-coloured uppercase section labels in
sidebars. Chevron or left-rule affordance on list rows.

### Phase 3 — Surfaces

Apply the card recipe to standalone panels and the auth/login card. Convert
selected/active states from filled pills to the 3px left rule. If the product
has a message or feed list, give the user-authored entries the `accent-soft`
fill with a cyan left rule and leave system entries flat.

### Phase 4 — Buttons and inputs

Square all buttons, 20px horizontal padding, teal → cyan hover at 300ms, arrow
prefix on primary CTAs. Square any circular action buttons (a floating "send"
FAB becomes a square button). Input frames pick up the accent focus border.

### Phase 5 — Typography

Roboto bold headings at the scale above, the cyan underline on page and panel
headings, and body copy in Open Sans. Then sweep the codebase for type settings
that were tuned for a different typeface:

```bash
grep -rn "letter-spacing: -0" src        # negative tracking → set to 0
grep -rn "font-weight: 5[0-9][0-9]" src  # variable-font weights → 400/500/600/700
```

### Phase 6 — Verify

- Run the project's existing tests and build.
- Screenshot light and dark at desktop width, plus one narrow breakpoint.
- Render the signed-out / auth screen too — it is easy to forget and it is often
  the first thing a user sees.

---

## Pitfalls

These are real failure modes, not hypotheticals.

- **Radius tokens that nothing consumes.** Adding `--radius: 0` changes nothing
  until every hardcoded `border-radius` references it. Inventory and migrate.
- **Runtime overrides beating your CSS.** Apps often set `<meta name="theme-color">`
  (and sometimes inline styles) from JavaScript on theme change. Grep for the old
  hex values in `.ts`/`.js`, not just stylesheets.
- **Dark chrome equal to dark background.** If `--chrome` and `--bg` are the same
  navy, the top bar vanishes. Keep chrome at `#00205B` while the page sits at
  `#001233`.
- **Double icons on CTAs.** A blanket `::before { content: '→' }` collides with
  buttons that already render an icon. Scope with `:not(:has(.icon))`. If `:has()`
  is unsupported the whole rule drops, which fails safe (no arrow) rather than
  producing duplicates.
- **Fixed-width logo containers.** A new brand mark with different internal
  geometry will overflow a container sized for the old one — check narrow
  breakpoint overrides, not just the default rule.
- **Typography tuned for the previous font.** Negative letter-spacing and
  variable-font weights like `550` are artifacts of geometric UI faces such as
  Inter. They read poorly in Roboto/Open Sans; normalise them.
- **Fallback colours inside `var()`.** Patterns like `var(--fg, #15223b)` keep
  stale colours alive wherever the variable is missing. Update the fallbacks too.

---

## Deliberate scope

This system comes from institutional/corporate web design, so it is airy by
default. When applying it to a **dense working tool** (dashboard, chat, admin
console), keep these deviations and say so explicitly:

- No hero banner or full-bleed gradient unless the product has a marketing surface.
- Generous section rhythm applies to rails, panels and empty states — not to the
  primary work column.
- Keep monospace for identifiers, telemetry and status chips.
- Keep the dark theme, re-skinned to navy, rather than dropping it.
