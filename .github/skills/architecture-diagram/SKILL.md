---
name: architecture-diagram
description: >-
  Analyze the current repository and create or update a self-contained,
  interactive HTML architecture explorer. Use for big-picture system views with
  selectable components, clickable flows, technical layers, runtime and data
  paths, deployment and networking topology, trust boundaries, operations, and
  evidence linked back to source files. The primary deliverable is functional
  HTML, not a Markdown or Mermaid-only diagram. WHEN: "create architecture
  diagram", "interactive architecture", "visualize this system", "show data
  flows", "map project components", "deployment topology", "network
  architecture", "technical architecture", "architecture explorer".
version: 1.0.0
tags: [architecture, html, visualization, data-flow, networking, security, documentation]
---

# Interactive Architecture Diagram

## Purpose

Create a trustworthy, interactive architecture explorer for the current
project. The result must help a new engineer understand:

- what the system does and where its boundaries are;
- which deployable and logical components exist;
- how requests, events, commands, and data move through the system;
- where data is stored, transformed, indexed, cached, or deleted;
- how the system is deployed and connected;
- where public, private, identity, and trust boundaries exist;
- how the system is operated, observed, scaled, and recovered;
- which repository evidence supports each architectural claim.

The primary deliverable is a polished, fully functional, self-contained HTML
page. A Mermaid diagram, Markdown document, static image, or generic box chart
does not satisfy this skill.

## Non-negotiable outcome

When invoked, do the work end to end:

1. Inspect the repository and collect evidence.
2. Build an explicit architecture model from that evidence.
3. Generate or update the interactive HTML page.
4. Verify the page and its interactions.
5. Report the output path and any material uncertainty.

Do not stop after describing a proposed diagram. Do not ask the user to convert
Markdown to HTML or wire up interactions later.

## Default output

- Follow an explicit path supplied by the user.
- Otherwise update an existing architecture explorer if the repository already
  has one.
- Otherwise create `docs/architecture.html`.
- Keep the final artifact in one HTML file with inline CSS, JavaScript, SVG, and
  data so it opens directly from disk and can be shared without a server.
- Do not overwrite an unrelated file. If the default path is occupied by
  unrelated content, choose `docs/architecture-explorer.html`.

## Ground truth before graphics

Architecture is not a file tree. Read enough implementation and infrastructure
to identify meaningful runtime, data, security, and operational boundaries.

### Evidence priority

Prefer evidence in this order:

1. Runtime code, dependency manifests, and application configuration.
2. Infrastructure as code, container definitions, deployment manifests, and
   CI/CD workflows.
3. Tests and executable setup scripts.
4. Current project documentation and ADRs.
5. Naming-based inference only when no stronger evidence exists.

When documentation disagrees with implementation, model the implementation and
call out the mismatch. Never turn a planned component into an implemented one.

### Inspect relevant surfaces

Adapt the investigation to the repository, but normally inspect:

- repository instructions and top-level README files;
- package and dependency manifests;
- application entry points and composition roots;
- frontend, API, worker, agent, batch, and scheduled-job boundaries;
- routes, commands, events, queues, topics, and external API clients;
- schemas, migrations, repositories, indexes, caches, and object storage;
- Dockerfiles, Compose, Kubernetes, Terraform, Bicep, Pulumi, or equivalent;
- identity, authorization, secret references, and trust-boundary configuration;
- ingress, DNS, load balancers, gateways, subnets, private endpoints, firewall
  rules, and egress paths;
- logging, metrics, tracing, health checks, alerts, retries, and dead-letter
  paths;
- tests that reveal request lifecycles or integration contracts.

Read example environment files when useful, but never read, copy, or render
actual secrets. Do not place credentials, tokens, connection strings, private
keys, sensitive tenant identifiers, or personal data in the artifact.

### Evidence labels

Every component and flow must carry one of these confidence labels:

- **Observed** - directly supported by implementation or infrastructure.
- **Inferred** - strongly implied but not explicitly declared.
- **Planned** - described as future work and clearly separated from the current
  architecture.
- **Unknown** - important information could not be established.

Use subdued styling for inferred, planned, and unknown elements. The page must
never present all claims as equally certain.

## Build a normalized architecture model

Keep architecture data separate from rendering logic. Embed one plain
JavaScript object or `application/json` script block as the source of truth.
Use stable, human-readable IDs. Do not duplicate component facts across views.

Use this shape as a guide, extending it only when the project needs more:

```js
const architecture = {
  meta: {
    project: "",
    summary: "",
    generatedAt: "",
    commit: "",
    scope: "",
    repositoryUrl: "",
    caveats: []
  },
  groups: [
    {
      id: "",
      name: "",
      kind: "layer | runtime | network-zone | trust-boundary | ownership",
      description: "",
      order: 0
    }
  ],
  components: [
    {
      id: "",
      name: "",
      kind: "",
      groupIds: [],
      summary: "",
      responsibilities: [],
      technologies: [],
      interfaces: [],
      dataOwned: [],
      deployment: [],
      security: [],
      operations: [],
      confidence: "observed | inferred | planned | unknown",
      evidence: [
        { path: "", symbol: "", line: null, note: "" }
      ]
    }
  ],
  flows: [
    {
      id: "",
      from: "",
      to: "",
      label: "",
      purpose: "",
      protocol: "",
      port: null,
      data: [],
      mode: "sync | async | stream | batch",
      trigger: "",
      authentication: "",
      encryption: "",
      boundaryCrossings: [],
      failureBehavior: "",
      confidence: "observed | inferred | planned | unknown",
      viewIds: [],
      evidence: [
        { path: "", symbol: "", line: null, note: "" }
      ]
    }
  ],
  views: [
    {
      id: "",
      name: "",
      description: "",
      groupIds: [],
      componentIds: [],
      flowIds: []
    }
  ]
};
```

Validate the model before rendering:

- all IDs are unique;
- every flow endpoint references an existing component;
- every group and view reference resolves;
- every observed component and flow has evidence;
- no displayed port, protocol, identity mechanism, or network route is invented;
- labels are concise enough to scan, with fuller explanation in the detail
  panel.

## Required architecture lenses

Use multiple selectable views rather than forcing every concern into one
overloaded canvas. Include each applicable lens:

1. **System context** - users, actors, system boundary, and external systems.
2. **Runtime / technical layers** - deployable services and meaningful logical
   layers such as presentation, API, orchestration, domain, integration, data,
   and operations. Derive layers from the project; do not force this example.
3. **Request and data flow** - important end-to-end journeys with numbered
   steps, direction, protocol, payload class, and synchronous/asynchronous
   semantics.
4. **Data architecture** - systems of record, ownership, movement,
   transformation, indexing, caching, retention, and read/write direction.
5. **Deployment and networking** - regions, compute, containers, ingress,
   public/private zones, subnets, private endpoints, external egress, and
   relevant ports when proven.
6. **Security and trust** - identities, authentication and authorization
   boundaries, secret stores, encryption boundaries, and privileged paths.
7. **Operations** - health, logs, metrics, traces, alerts, scaling, retries,
   queues, dead letters, backups, and recovery where present.

For AI or agent systems, add an **AI execution** lens covering model endpoints,
agent/runtime boundaries, tool calls, retrieval, prompt sources, memory,
guardrails, evaluation, and telemetry. For event-driven systems, add an
**event topology** lens. Omit non-applicable lenses rather than filling them
with placeholders.

## Information density and visual hierarchy

The first view must communicate the big picture in under a minute. Show
deployable units and major managed services, not every class or source file.
Use drill-down details for lower-level information.

Within a view:

- group components into visibly labeled layers or zones;
- draw directional, labeled flows;
- distinguish sync, async, stream, and batch paths with line style plus text,
  never color alone;
- render trust and network boundaries as containers, not ordinary nodes;
- place external systems outside the project boundary;
- make primary paths visually stronger than administrative or telemetry paths;
- keep labels readable at the default zoom;
- minimize crossings and route edges behind nodes;
- use deterministic layout so regeneration produces a stable diagram.

Prefer native SVG for edges, markers, boundaries, and scalable rendering.
Components may be accessible SVG groups or semantic HTML elements coordinated
with an SVG edge layer. Do not use a raster image as the interactive canvas.

## Minimum interaction contract

The generated page is incomplete unless all of these work:

- **View switcher:** change among the applicable architecture lenses.
- **Selectable components:** click or keyboard-activate any component.
- **Details panel:** show responsibility, technology, interfaces, data,
  deployment, security, operations, confidence, and source evidence.
- **Selectable flows:** click a connection to inspect protocol, data, trigger,
  security, boundary crossings, failure behavior, and evidence.
- **Dependency trace:** highlight immediate and transitive upstream,
  downstream, or both; dim unrelated nodes and flows.
- **Search:** find components, technologies, interfaces, data, and evidence
  paths; selecting a result focuses the diagram.
- **Filters:** toggle layers/zones, component kinds, flow modes, confidence, and
  secondary flows without corrupting the active selection.
- **Pan and zoom:** pointer drag, wheel/trackpad zoom, zoom controls, reset, and
  fit-to-view.
- **Legend:** explain node kinds, boundary types, flow styles, confidence, and
  selection states.
- **Deep link:** keep the active view and selected component or flow in the URL
  hash so the current state can be shared.
- **Responsive layout:** work on wide desktop screens and narrow mobile screens;
  the detail panel becomes a drawer or stacked panel when needed.
- **Keyboard support:** logical tab order, visible focus, Enter/Space activation,
  Escape to clear/close, and named controls.

Useful additions when they improve the project story include a minimap, a
numbered journey player, collapse/expand for groups, full-screen mode, SVG
export, and a reduced-motion-safe flow animation. Do not add features that are
decorative or unreliable.

## Page structure

Use a clear application shell:

1. Header with project name, short purpose, snapshot commit/date, and theme
   control.
2. Compact overview with system boundary, runtime count, data stores, external
   dependencies, and material caveats.
3. Toolbar with view selector, search, filters, trace direction, fit, and reset.
4. Main diagram canvas with layer/zone labels and legend.
5. Component/flow detail panel.
6. Evidence and assumptions section explaining confidence levels and source
   coverage.

Avoid dashboard clutter. Architecture is the hero; summary cards and controls
support it.

## Required visual theme

When available, use the `web-artifacts-builder` skill and its Clawpilot theme.
For a directly generated HTML file, the following contract is mandatory.

Place this theme detection script before any other JavaScript:

```html
<script>
  (() => {
    const param = new URLSearchParams(window.location.search).get("scoutTheme");
    const theme =
      param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", theme);
  })();
</script>
```

Copy these variables exactly into the page style block:

```css
:root {
  color-scheme: light;
  --cp-bg: #f7f4ef;
  --cp-bg-elevated: #fcfbf8;
  --cp-surface: #ffffff;
  --cp-surface-soft: #f5f5f5;
  --cp-border: #dedede;
  --cp-border-strong: #919191;
  --cp-text: #242424;
  --cp-text-muted: #5c5c5c;
  --cp-text-soft: #6f6f6f;
  --cp-accent: #b11f4b;
  --cp-accent-hover: #9a1a41;
  --cp-accent-soft: rgba(177, 31, 75, 0.08);
  --cp-accent-fg: #ffffff;
  --cp-success: #16a34a;
  --cp-danger: #dc2626;
  --cp-warning: #f59e0b;
  --cp-link: #0078d4;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
  --cp-overlay: rgba(255, 255, 255, 0.8);
  --cp-panel: rgba(255, 255, 255, 0.86);
  --cp-panel-strong: rgba(255, 255, 255, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.55);
  --cp-highlight: rgba(177, 31, 75, 0.12);
}
html[data-theme="dark"] {
  color-scheme: dark;
  --cp-bg: #3d3b3a;
  --cp-bg-elevated: #343231;
  --cp-surface: #292929;
  --cp-surface-soft: #2e2e2e;
  --cp-border: #474747;
  --cp-border-strong: #5f5f5f;
  --cp-text: #dedede;
  --cp-text-muted: #919191;
  --cp-text-soft: #b0b0b0;
  --cp-accent: #fd8ea1;
  --cp-accent-hover: #fb7b91;
  --cp-accent-soft: rgba(253, 142, 161, 0.14);
  --cp-accent-fg: #1a1a1a;
  --cp-success: #4ade80;
  --cp-danger: #f87171;
  --cp-warning: #fbbf24;
  --cp-link: #4da6ff;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
  --cp-overlay: rgba(41, 41, 41, 0.88);
  --cp-panel: rgba(41, 41, 41, 0.72);
  --cp-panel-strong: rgba(41, 41, 41, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.04);
  --cp-highlight: rgba(253, 142, 161, 0.12);
}
```

Use only `var(--cp-*)` tokens for colors in component styles. Derived colors
may use `color-mix()` with those tokens. Use line patterns, shapes, labels, and
icons as well as color to distinguish architectural semantics.

Typography:

```css
font-family: "Segoe UI", Aptos, Calibri, -apple-system, BlinkMacSystemFont, sans-serif;
```

Use `Consolas, "Courier New", Courier, monospace` for code, paths, protocols,
ports, and identifiers. Use mostly 10px radii, 16px cards, subtle borders, and
restrained shadows. Keep both light and dark themes first class. Respect
`prefers-reduced-motion`.

## Implementation rules

- The final page must work offline from `file://`.
- Do not use CDN scripts, remote fonts, remote images, or runtime API calls.
- If a framework or graph library is used during development, bundle it into
  the final HTML. Prefer a dependency-free implementation when it remains clear
  and maintainable.
- Keep data, layout, rendering, interaction, and formatting functions separate.
- Use semantic HTML and accessible names. SVG nodes need focusability, roles,
  and labels; also provide a navigable component list when browser SVG keyboard
  behavior is insufficient.
- Render repository-derived text with `textContent` or equivalent safe DOM APIs.
  Do not interpolate untrusted text into `innerHTML`.
- Use event delegation rather than attaching redundant listeners to every
  rerendered element.
- Make layout deterministic and compute bounds for fit-to-view.
- Use `vector-effect="non-scaling-stroke"` where zooming would otherwise make
  borders or flow lines unreadable.
- Preserve selection and viewport where sensible when filters change.
- Put edges behind nodes and enlarge invisible edge hit targets so connections
  are easy to select.
- Explain acronyms on first use in details or the legend.
- Include a visible "Snapshot" label with commit SHA when Git metadata is
  available; architecture can drift after generation.

## Progressive detail

Each component card on the canvas should contain only:

- component name;
- concise type or runtime label;
- one-line responsibility;
- small confidence/status cue.

The detail panel should answer:

- Why does this component exist?
- What does it own?
- What calls it, and what does it call?
- Which protocols and contracts does it expose?
- What data does it read, write, transform, or retain?
- Where and how does it run?
- Which identity and security controls apply?
- How is it observed and operated?
- Which files or symbols prove these claims?

Evidence entries should display repository-relative paths. If the Git remote and
snapshot commit are available, link evidence to the corresponding immutable
repository URL. Do not fabricate links for local-only repositories.

## Verification

Do not declare completion until the artifact is persistent and verified.

### Content checks

- The big-picture view matches actual runtime and deployment boundaries.
- Major user journeys can be followed from origin to final data store or
  response.
- Data ownership and read/write direction are visible.
- External systems and project-owned components are unambiguous.
- Public/private and trust boundaries are shown when they exist.
- Architecture claims include evidence and confidence.
- Planned work is visually distinct from implemented architecture.

### Functional checks

- Open the final file directly and confirm there are no console errors or
  missing assets.
- Exercise every view, component selection, flow selection, search, filter,
  dependency trace, detail close, pan, zoom, fit, reset, theme, and deep link.
- Confirm hidden components do not leave orphaned flows.
- Confirm filters can return to the complete graph.
- Confirm focus remains visible and keyboard activation works.
- Confirm reduced-motion mode removes nonessential animation.

### Visual checks

- Inspect at a wide desktop size and a narrow mobile size.
- Inspect light and dark themes.
- Confirm labels do not overlap nodes or clip at ordinary zoom.
- Confirm boundaries contain their intended components.
- Confirm primary flows are legible and edge crossings are reasonable.
- Confirm the detail panel does not cover the selected node without a way to
  refocus or fit the canvas.

Use an available browser canvas or browser automation for visual verification.
If neither is available, still perform structural checks and say which visual
checks could not be completed.

## Completion response

Lead with the created or updated HTML path. Briefly name the included views and
interactions, then disclose only material inferred, planned, or unknown areas.
Do not paste the diagram into Markdown and do not repeat the full architecture
model in the response.

## Failure patterns to reject

- A Mermaid diagram wrapped in an HTML page with no real interaction.
- A screenshot or canvas image with invisible, non-selectable components.
- Generic boxes named "Frontend", "Backend", and "Database" without repository
  evidence or meaningful responsibility.
- One overloaded view mixing runtime, networking, data, and security until none
  is readable.
- Unlabeled arrows or arrows with no inspectable flow metadata.
- Colors as the only way to distinguish flow or component types.
- Hard-coded coordinates that clip as content changes.
- A page that requires internet access or a development server.
- Architecture claims derived only from README prose while implementation and
  infrastructure are available.
- Exposure of secrets, private configuration values, or personal data.
