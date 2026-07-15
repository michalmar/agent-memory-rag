---
name: frontend-design
description: >-
  Design or redesign compact, production-ready frontend UI with strong hierarchy,
  icon-led navigation, quiet neutral surfaces, truthful interaction states,
  responsive behavior, accessibility, and first-class light and dark themes. Use
  when building, styling, refreshing, or reviewing websites, web apps,
  dashboards, conversational interfaces, landing pages, and UI components.
  WHEN: "design frontend", "redesign UI", "improve UX", "modernize page",
  "style this app", "build landing page", "make interface look polished".
version: 1.1.0
tags: [frontend, ui, ux, web-design, accessibility, responsive, theming]
---

# Frontend Design: Compact Product UI

Design like a product designer working in an existing system. Preserve behavior,
learn the information architecture, then improve density, hierarchy, navigation,
copy, responsiveness, and interaction design as one coherent system.

The default application direction is compact and Linear-like: flat tonal planes,
thin dividers, precise spacing, icon-led chrome, restrained radii, and one quiet
accent. Minimal means exact, not empty. Light and dark modes are first-class from
the start.

## Inspect Before Designing

Before changing markup or CSS:

1. Run and inspect the current product.
2. Inventory routes, navigation, primary tasks, state, API-backed functionality,
   streaming events, loading/error paths, dialogs, and responsive behavior.
3. Identify what must be preserved and what is merely presentation.
4. When a visual reference is supplied, match its density, alignment, hierarchy,
   and interaction model before borrowing decorative details.
5. Never invent backend capabilities or imply a state the product cannot observe.

If the brief does not define the product, choose one concrete product and state:

- what it is,
- who uses it,
- the single main job the page must do.

## Core Design Rules

### Choose the Right Focal Point

Marketing pages may need a hero. Workflow applications usually do not. Do not
force oversized headlines, diagrams, starter cards, or explanatory copy into the
primary workspace. Let the task, data, or active conversation be the focal point.

Empty states should be quiet: one familiar icon, a short title, and one sentence
that points to the first useful action. Move conceptual explanations to onboarding
or documentation instead of leaving permanent clutter in the work area.

### Type Give Character, But Stay Familiar

Use Inter or the platform system stack for interface and content. Use monospace
only for code, IDs, counts, timestamps, shortcuts, and compact utility labels.

```css
--font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
  Helvetica, Arial, sans-serif;
--font-mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas,
  monospace;
```

Make personality with scale, weight, width, tracking, line height, and contrast.
Do not add novelty fonts unless the subject truly requires one.

Define clear roles: display, heading, body, label, caption, data. Type should be
memorable because treatment precise, not because font weird.

### Structure Must Mean Thing

Layout carries information. Number, eyebrow, divider, badge, label, and rule must
tell human something true. Decoration with no job gets clubbed away.

Do not use `01 / 02 / 03` unless content is real sequence, process, or timeline.
If order no matter, number no belong.

### Color Show Path

Color must guide, not confuse. Most screen stays neutral. Use accent for primary
action, active navigation, focus, selected state, useful status, and important
links. Do not paint every card different color. Do not use color alone to carry
meaning; pair with text, icon, shape, or position.

Start with this restrained product palette. Adjust only when product identity or
contrast needs demand it.

| Role | Light | Dark |
| --- | --- | --- |
| Canvas | `#F6F7F8` | `#0E0F10` |
| Surface | `#FFFFFF` | `#111315` |
| Raised surface | `#EFF0F2` | `#191B1E` |
| Primary text | `#202126` | `#E7E8EB` |
| Secondary text | `#676B73` | `#9599A2` |
| Border | `#DEDFE3` | `#292B2F` |
| Brand / action | `#4A57D8` | `#8B96FF` |
| Success | `#2E725F` | `#79C6AA` |
| Danger | `#AD4141` | `#FF9C9C` |

Keep accent coverage small. Status colors appear only for real status. Do not give
one content category a different color unless that color communicates a stable,
useful distinction.

Use semantic tokens, never scatter raw hex through components:

```css
:root {
  color-scheme: light dark;
  --canvas: #f6f7f8;
  --surface: #ffffff;
  --surface-raised: #eff0f2;
  --text: #202126;
  --text-muted: #676b73;
  --border: #dedfe3;
  --accent: #4a57d8;
  --accent-foreground: #ffffff;
  --success: #2e725f;
  --danger: #ad4141;
}

@media (prefers-color-scheme: dark) {
  :root {
    --canvas: #0e0f10;
    --surface: #111315;
    --surface-raised: #191b1e;
    --text: #e7e8eb;
    --text-muted: #9599a2;
    --border: #292b2f;
    --accent: #8b96ff;
    --accent-foreground: #0e0f10;
    --success: #79c6aa;
    --danger: #ff9c9c;
  }
}
```

Follow system theme by default. If app has theme control, support `light`,
`dark`, and `system`. Save human choice. Avoid pure black and pure white across
large areas. Test text, controls, focus rings, borders, charts, disabled states,
and status colors in both themes. Meet WCAG AA contrast at minimum.

### Compact Product UI Contract

For desktop workflow applications:

- Keep the top bar approximately `48–56px` high.
- Use dedicated navigation and context rails where the data model warrants them.
- Keep repeated rail actions compact and group them near the content they affect.
- Use familiar icons without text for repeated global actions. Every icon-only
  control needs an accessible name and hover/focus detail. Keep labels for novel,
  ambiguous, destructive, or high-stakes actions.
- Prefer flat surfaces separated by 1px rules. Reserve shadows for drawers,
  popovers, menus, and other elevated layers.
- Use restrained radii, usually `6–10px`. Do not turn every section into a card.
- Keep the primary reading or conversation column bounded while allowing the
  canvas around it to remain quiet.
- Place controls in context. A mode or agent that applies to message composition
  belongs in the composer, not in a distant header.
- On narrow screens, keep the main task primary and move secondary rails into
  mutually exclusive drawers.

Compact does not mean cramped. Related things sit close; different concepts get a
divider or meaningful gap. Hierarchy comes from alignment, spacing, weight, and
contrast before effects.

### Conversational and AI Interfaces

- Render conversation as a thread, not a stack of chat bubbles. Keep user and
  agent messages aligned left and distinguish speakers with a compact icon/avatar,
  name, and spacing.
- Do not use decorative gradients, glowing avatars, oversized assistant cards, or
  colorful message balloons.
- Keep the composer inside the conversation surface rather than placing it in a
  contrasting footer pane. Start multiline input at roughly three lines, let it
  grow upward with content, and cap growth before enabling internal scrolling.
- Place compact, borderless agent and model dropdowns at the composer's lower
  left and the familiar icon-only send action at the lower right. Preserve clear
  hover, focus, unavailable, and thread-locked states.
- Never imply model routing that the backend does not support. Keep the configured
  agent default visible and label future model choices as unavailable until the
  request contract can carry them.
- Show `Working` only while a request is pending and `Writing` only while text is
  actually streaming. Never fabricate chain-of-thought, reasoning progress, or
  backend state.
- Tool and citation results may use a bordered contained surface, but keep them
  neutral and subordinate to the transcript. Do not add a colored edge or badge
  unless it communicates semantic status.
- Keep memory, grounding, and retrieval indicators only when they convey real,
  actionable state. Do not add badges merely to advertise architecture.

### Motion Must Earn Food

Use motion only when it explains change, confirms action, preserves context, or
adds one intentional moment. Good choices: one page-load sequence, one reveal,
clear hover feedback, or transition between related states.

One orchestrated moment often stronger than many wiggles. Too much motion smells
like machine-made UI. Respect `prefers-reduced-motion`. Never block task with
animation.

### Complexity Match Vision

Big expressive vision needs enough craft to work. Minimal vision needs exact
spacing, type, state, and detail. Minimal no mean unfinished. Elegance means
chosen vision done well.

### Words Are Design Material

Brief may have no real copy. You create useful copy, not placeholder sludge.
Generic words make generic design. Think about writing before build.

## Work in Two Passes

### Pass One: Audit and Direction

Make compact design plan from brief:

1. **Color:** Give 4-6 named semantic colors with hex values for both themes.
   Start from palette above, then tailor only with reason.
2. **Type:** Define display, body, and utility roles using familiar sans and mono
   stacks.
   State sizes, weights, and spacing that create character.
3. **Layout:** Map existing product functions into navigation, primary workspace,
   contextual panels, and responsive drawers. Use a small ASCII wireframe when it
   improves clarity.
4. **Interaction:** List the important empty, loading, streaming, error, active,
   disabled, locked, drawer, dialog, and touch states.
5. **Signature:** Add a memorable element only when it helps the subject or task.
   A distinctive interaction or spatial pattern is enough; decoration is not
   mandatory.

### Stop. Critique Plan.

Compare plan to brief. Ask: could same plan appear for any similar product? If
yes, generic bad. Change generic part. State what changed and why.

Current machine-made designs often fall into three caves:

1. warm cream canvas, contrast serif, clay accent;
2. near-black canvas, one acid green or hot red accent;
3. newspaper grid, hairline rules, square corners, dense columns.

These can be right when brief asks. But they are defaults, not automatic good.
When brief gives direction, obey brief exactly. When brief leaves freedom, spend
freedom on subject-specific choice, not common cave.

Only build after the direction feels specific. Preserve application behavior and
API contracts while changing presentation.

### Pass Two: Build and Critique Again

Build complete experience, not pretty screenshot. Include responsive states,
loading, empty, error, success, hover, focus, active, disabled, and selected
states where relevant.

Watch CSS specificity. Broad class selector and element selector can fight,
especially for section padding, buttons, and calls to action. Prefer low,
predictable specificity, component boundaries, and semantic tokens. No mystery
override tower.

Exercise the real product rather than reviewing only an empty-state screenshot.
Verify the primary task, navigation, drawers, dialogs, theme persistence, error
paths, and any request/streaming state the backend supports.

## Restraint and Self-Critique

Spend boldness in one place: signature element. Everything around it quiet and
disciplined. Remove decoration that serves no brief. No risk can itself be risk,
but five risks become noise.

Quality floor is silent and mandatory:

- works from small mobile to wide desktop,
- keyboard path complete,
- focus always visible,
- semantic HTML and accessible names used,
- icon-only controls explain themselves on hover and focus,
- filled controls have a focus ring that contrasts with their fill,
- `aria-current` describes the current item rather than incorrectly announcing a
  page,
- informative icon labels use a role that assistive technology can announce,
- touch targets comfortable,
- reduced motion respected,
- light and dark modes both polished,
- no layout shift from fonts, images, or loading state.

Critique while building. At minimum, inspect `1440×900` and `390×844` in light and
dark modes. Capture an empty state, a real content state, and any genuine
working/streaming state. Run the existing build and responsive checks, then use an
independent code review when available. Before finishing, remove one unnecessary
accessory.

## Cave Rules for Interface Writing

Words exist to make thing easier to understand and use. Words no decoration.
Before writing, ask what human needs know and what wording helps human move.

Write from human side of screen. Name what human recognizes and controls, not
system plumbing. Human manages notifications, not webhook configuration. Say
what thing does in plain terms. Specific beats clever.

Use active voice. Control says exact result: `Save changes`, not `Submit`. Keep
action name same through flow. Button says `Publish`; confirmation says
`Published`. Consistent words become trail markers through product.

Failure and empty state give direction:

- Error says what happened and how human can fix it.
- Error no vague. Error no fake apology.
- Empty screen invites useful first action.

Use plain verbs, sentence case, and no filler. Match tone to brand and audience.
Each element has one job. Label labels. Example demonstrates. Help text helps.
Nothing secretly tries to do two jobs.
