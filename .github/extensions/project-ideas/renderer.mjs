import { summarizeIdeas } from "./ideas.mjs";
import { getExpectedAction } from "./launch.mjs";

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function renderInlineMarkdown(value) {
    const tokenPattern =
        /\[([^\]]+)\]\(([^)\s]+)\)|`([^`]+)`|\*\*([^*]+)\*\*/g;
    let html = "";
    let previousIndex = 0;

    for (const match of value.matchAll(tokenPattern)) {
        html += escapeHtml(value.slice(previousIndex, match.index));

        if (match[1] && match[2]) {
            const label = escapeHtml(match[1]);
            if (/^https?:\/\//i.test(match[2])) {
                html += `<a href="${escapeHtml(match[2])}" target="_blank" rel="noreferrer">${label}</a>`;
            } else {
                html += `<span class="file-reference" title="${escapeHtml(match[2])}">${label}</span>`;
            }
        } else if (match[3]) {
            html += `<code>${escapeHtml(match[3])}</code>`;
        } else {
            html += `<strong>${escapeHtml(match[4])}</strong>`;
        }

        previousIndex = match.index + match[0].length;
    }

    return html + escapeHtml(value.slice(previousIndex));
}

function renderDescription(description) {
    if (!description.trim()) {
        return '<p class="empty-description">No description provided.</p>';
    }

    return description
        .trim()
        .split(/\n\s*\n/)
        .map((paragraph) =>
            `<p>${renderInlineMarkdown(
                paragraph.replace(/\s*\n\s*/g, " "),
            )}</p>`,
        )
        .join("");
}

function renderMetadata(idea) {
    const items = [
        ["Date", idea.metadata.date],
        ["Author", idea.metadata.author],
        ["Implemented", idea.metadata.implemented],
        ["Implemented date", idea.metadata.implemented_date],
    ].filter(([, value]) => value);

    return items
        .map(
            ([label, value]) =>
                `<span class="meta-item"><span>${escapeHtml(label)}</span>${escapeHtml(value)}</span>`,
        )
        .join("");
}

function renderPlan(idea) {
    if (!idea.plan) {
        if (idea.implemented) {
            return "";
        }
        return `
            <div class="plan-note plan-note-missing">
                <span class="plan-icon" aria-hidden="true">?</span>
                <div>
                    <strong>Planning needed</strong>
                    <span>No implementation plan is attached yet.</span>
                </div>
            </div>`;
    }

    const target = idea.plan.target
        ? `<code title="${escapeHtml(idea.plan.target)}">${escapeHtml(idea.plan.target)}</code>`
        : "";
    return `
        <div class="plan-note">
            <span class="plan-icon" aria-hidden="true">P</span>
            <div>
                <strong>${escapeHtml(idea.plan.label)}</strong>
                ${target}
            </div>
        </div>`;
}

function renderLaunchButton(idea) {
    if (idea.implemented) {
        return `
            <span class="completed-label">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.8 4.2a.75.75 0 0 1 0 1.1l-6.3 6.3a.75.75 0 0 1-1.1 0L2.2 7.4a.75.75 0 1 1 1.1-1.1l3.6 3.7 5.8-5.8a.75.75 0 0 1 1.1 0Z"/></svg>
                Implemented
            </span>`;
    }

    const action = getExpectedAction(idea);
    const label =
        action === "implementation"
            ? "Start implementation"
            : "Start planning";
    return `
        <button
            class="launch-button"
            type="button"
            data-launch
            data-idea-id="${escapeHtml(idea.id)}"
            data-action="${action}"
        >
            ${escapeHtml(label)}
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8.2 2.2a.75.75 0 0 1 1.1 0l5.2 5.3a.75.75 0 0 1 0 1l-5.2 5.3a.75.75 0 1 1-1.1-1.1l4-4H2a.75.75 0 0 1 0-1.5h10.2l-4-4a.75.75 0 0 1 0-1.1Z"/></svg>
        </button>`;
}

function renderIdeaCard(idea) {
    const searchText = [
        idea.title,
        idea.description,
        idea.plan?.label,
        idea.plan?.target,
        idea.metadata.author,
    ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

    return `
        <article class="idea-card${idea.implemented ? " idea-card-implemented" : ""}" data-search="${escapeHtml(searchText)}">
            <div class="card-topline">
                <span class="status-badge ${idea.archived ? "status-complete" : "status-current"}">
                    ${idea.archived ? "Archived" : "Current"}
                </span>
                ${idea.plan ? '<span class="plan-badge">Plan attached</span>' : ""}
            </div>
            <h2>${escapeHtml(idea.title)}</h2>
            <div class="description">${renderDescription(idea.description)}</div>
            ${renderPlan(idea)}
            <div class="card-footer">
                <div class="metadata">${renderMetadata(idea)}</div>
                ${renderLaunchButton(idea)}
            </div>
        </article>`;
}

function renderPanel(view, ideas, activeView) {
    const title = view === "active" ? "Current ideas" : "Archive";
    const description =
        view === "active"
            ? "Open work, ordered as it appears in IDEAS.md."
            : "Implemented ideas retained as project history.";
    const cards = ideas.map(renderIdeaCard).join("");

    return `
        <section
            class="ideas-panel"
            id="panel-${view}"
            role="tabpanel"
            aria-labelledby="tab-${view}"
            ${view === activeView ? "" : "hidden"}
        >
            <div class="section-heading">
                <div>
                    <h2>${title}</h2>
                    <p>${description}</p>
                </div>
                <span class="section-count">${ideas.length}</span>
            </div>
            <div class="ideas-grid">${cards}</div>
            <div class="empty-state" data-empty="${view}" hidden>
                <strong>No matching ideas</strong>
                <span>Try a different search term.</span>
            </div>
        </section>`;
}

export function renderIdeasHtml({ ideas, initialView, token }) {
    const summary = summarizeIdeas(ideas);
    const activeIdeas = ideas.filter((idea) => !idea.archived);
    const archivedIdeas = ideas.filter((idea) => idea.archived);
    const config = JSON.stringify({ initialView, token });

    return `<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Project ideas</title>
    <style>
        * { box-sizing: border-box; }
        :root { color-scheme: light dark; }
        body {
            margin: 0;
            background: var(--background-color-default, #ffffff);
            color: var(--text-color-default, #1f2328);
            font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
            font-size: var(--text-body-medium, 14px);
            line-height: var(--leading-body-medium, 20px);
        }
        button, input { font: inherit; }
        button { color: inherit; }
        .shell {
            width: min(1180px, 100%);
            margin: 0 auto;
            padding: 36px 28px 56px;
        }
        .hero {
            position: relative;
            overflow: hidden;
            padding: 32px;
            border: 1px solid var(--border-color-default, #d0d7de);
            border-radius: 18px;
            background:
                radial-gradient(circle at 92% 12%, color-mix(in srgb, var(--true-color-blue, #0969da) 18%, transparent), transparent 32%),
                color-mix(in srgb, var(--text-color-default, #1f2328) 3%, var(--background-color-default, #ffffff));
        }
        .eyebrow {
            margin-bottom: 8px;
            color: var(--true-color-blue, #0969da);
            font-size: 11px;
            font-weight: var(--font-weight-semibold, 600);
            letter-spacing: .13em;
            text-transform: uppercase;
        }
        h1 {
            max-width: 720px;
            margin: 0;
            font-family: var(--font-sans-display, var(--font-sans, sans-serif));
            font-size: clamp(30px, 5vw, 48px);
            font-weight: var(--font-weight-semibold, 600);
            line-height: 1.05;
            letter-spacing: -.035em;
        }
        .hero-copy {
            max-width: 620px;
            margin: 14px 0 0;
            color: var(--text-color-muted, #59636e);
            font-size: 15px;
        }
        .stats {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 28px;
        }
        .stat {
            min-width: 118px;
            padding: 12px 14px;
            border: 1px solid var(--border-color-default, #d0d7de);
            border-radius: 10px;
            background: color-mix(in srgb, var(--background-color-default, #ffffff) 86%, transparent);
        }
        .stat strong {
            display: block;
            font-size: 20px;
            line-height: 1.1;
        }
        .stat span {
            color: var(--text-color-muted, #59636e);
            font-size: 12px;
        }
        .toolbar {
            position: sticky;
            top: 0;
            z-index: 5;
            display: flex;
            align-items: center;
            gap: 12px;
            margin: 22px 0;
            padding: 10px;
            border: 1px solid var(--border-color-default, #d0d7de);
            border-radius: 12px;
            background: color-mix(in srgb, var(--background-color-default, #ffffff) 94%, transparent);
            backdrop-filter: blur(14px);
        }
        .tabs {
            display: flex;
            gap: 4px;
            padding: 3px;
            border-radius: 9px;
            background: color-mix(in srgb, var(--text-color-default, #1f2328) 6%, transparent);
        }
        .tab {
            padding: 7px 11px;
            border: 0;
            border-radius: 7px;
            background: transparent;
            color: var(--text-color-muted, #59636e);
            cursor: pointer;
            font-weight: var(--font-weight-semibold, 600);
        }
        .tab[aria-selected="true"] {
            background: var(--background-color-default, #ffffff);
            color: var(--text-color-default, #1f2328);
            box-shadow: 0 1px 3px color-mix(in srgb, var(--text-color-default, #1f2328) 12%, transparent);
        }
        .search-wrap {
            position: relative;
            flex: 1;
            min-width: 140px;
        }
        .search-wrap svg {
            position: absolute;
            top: 50%;
            left: 11px;
            width: 15px;
            fill: var(--text-color-muted, #59636e);
            transform: translateY(-50%);
            pointer-events: none;
        }
        .search {
            width: 100%;
            height: 34px;
            padding: 0 12px 0 34px;
            border: 1px solid transparent;
            border-radius: 8px;
            outline: none;
            background: transparent;
            color: var(--text-color-default, #1f2328);
        }
        .search:focus {
            border-color: var(--color-focus-outline, #0969da);
            background: var(--background-color-default, #ffffff);
            box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-focus-outline, #0969da) 24%, transparent);
        }
        .refresh {
            display: grid;
            width: 34px;
            height: 34px;
            place-items: center;
            border: 0;
            border-radius: 8px;
            background: transparent;
            cursor: pointer;
        }
        .refresh:hover { background: color-mix(in srgb, var(--text-color-default, #1f2328) 7%, transparent); }
        .refresh svg { width: 16px; fill: currentColor; }
        .section-heading {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 16px;
            margin: 28px 2px 14px;
        }
        .section-heading h2 {
            margin: 0;
            font-size: 18px;
            font-weight: var(--font-weight-semibold, 600);
        }
        .section-heading p {
            margin: 4px 0 0;
            color: var(--text-color-muted, #59636e);
        }
        .section-count {
            display: grid;
            min-width: 30px;
            height: 30px;
            padding: 0 8px;
            place-items: center;
            border: 1px solid var(--border-color-default, #d0d7de);
            border-radius: 999px;
            color: var(--text-color-muted, #59636e);
            font-weight: var(--font-weight-semibold, 600);
        }
        .ideas-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 14px;
        }
        .idea-card {
            display: flex;
            min-width: 0;
            min-height: 310px;
            flex-direction: column;
            padding: 20px;
            border: 1px solid var(--border-color-default, #d0d7de);
            border-radius: 14px;
            background: var(--background-color-default, #ffffff);
            box-shadow: 0 1px 1px color-mix(in srgb, var(--text-color-default, #1f2328) 5%, transparent);
            transition: border-color .15s ease, transform .15s ease, box-shadow .15s ease;
        }
        .idea-card:hover {
            border-color: color-mix(in srgb, var(--true-color-blue, #0969da) 55%, var(--border-color-default, #d0d7de));
            box-shadow: 0 8px 26px color-mix(in srgb, var(--text-color-default, #1f2328) 9%, transparent);
            transform: translateY(-1px);
        }
        .idea-card-implemented { min-height: 270px; }
        .card-topline {
            display: flex;
            align-items: center;
            gap: 7px;
            min-height: 23px;
        }
        .status-badge, .plan-badge {
            display: inline-flex;
            align-items: center;
            min-height: 22px;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: var(--font-weight-semibold, 600);
        }
        .status-current {
            background: var(--true-color-blue-muted, #ddf4ff);
            color: var(--true-color-blue, #0969da);
        }
        .status-complete {
            background: color-mix(in srgb, var(--text-color-default, #1f2328) 7%, transparent);
            color: var(--text-color-muted, #59636e);
        }
        .plan-badge {
            border: 1px solid var(--border-color-default, #d0d7de);
            color: var(--text-color-muted, #59636e);
        }
        .idea-card h2 {
            margin: 14px 0 8px;
            font-family: var(--font-sans-display, var(--font-sans, sans-serif));
            font-size: 20px;
            font-weight: var(--font-weight-semibold, 600);
            line-height: 1.25;
            letter-spacing: -.015em;
        }
        .description {
            color: var(--text-color-muted, #59636e);
        }
        .description p { margin: 0 0 8px; }
        .description a {
            color: var(--true-color-blue, #0969da);
            text-decoration: none;
        }
        .description a:hover { text-decoration: underline; }
        .description code, .plan-note code {
            font-family: var(--font-mono, "SFMono-Regular", Consolas, monospace);
            font-size: var(--text-code-inline, 12px);
        }
        .file-reference {
            border-bottom: 1px dotted var(--text-color-muted, #59636e);
        }
        .empty-description { font-style: italic; }
        .plan-note {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            margin-top: 12px;
            padding: 11px;
            border: 1px solid color-mix(in srgb, var(--true-color-blue, #0969da) 28%, var(--border-color-default, #d0d7de));
            border-radius: 10px;
            background: color-mix(in srgb, var(--true-color-blue, #0969da) 6%, transparent);
        }
        .plan-note-missing {
            border-color: var(--border-color-default, #d0d7de);
            background: color-mix(in srgb, var(--text-color-default, #1f2328) 3%, transparent);
        }
        .plan-icon {
            display: grid;
            width: 23px;
            height: 23px;
            flex: 0 0 auto;
            place-items: center;
            border-radius: 6px;
            background: var(--true-color-blue, #0969da);
            color: var(--color-white, #ffffff);
            font-size: 11px;
            font-weight: 700;
        }
        .plan-note-missing .plan-icon {
            background: color-mix(in srgb, var(--text-color-default, #1f2328) 12%, transparent);
            color: var(--text-color-muted, #59636e);
        }
        .plan-note strong, .plan-note span, .plan-note code {
            display: block;
            min-width: 0;
        }
        .plan-note strong {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .plan-note span, .plan-note code {
            overflow: hidden;
            margin-top: 2px;
            color: var(--text-color-muted, #59636e);
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .card-footer {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 14px;
            margin-top: auto;
            padding-top: 18px;
        }
        .metadata {
            display: flex;
            min-width: 0;
            flex-wrap: wrap;
            gap: 5px 12px;
        }
        .meta-item {
            color: var(--text-color-muted, #59636e);
            font-size: 11px;
            white-space: nowrap;
        }
        .meta-item span {
            margin-right: 4px;
            color: color-mix(in srgb, var(--text-color-muted, #59636e) 75%, transparent);
        }
        .launch-button {
            display: inline-flex;
            min-height: 36px;
            flex: 0 0 auto;
            align-items: center;
            gap: 8px;
            padding: 7px 12px;
            border: 1px solid var(--true-color-blue, #0969da);
            border-radius: 8px;
            background: var(--true-color-blue, #0969da);
            color: var(--color-white, #ffffff);
            cursor: pointer;
            font-weight: var(--font-weight-semibold, 600);
        }
        .launch-button:hover { filter: brightness(1.08); }
        .launch-button:disabled {
            cursor: wait;
            filter: grayscale(.45);
            opacity: .7;
        }
        .launch-button svg {
            width: 15px;
            fill: currentColor;
        }
        .completed-label {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: var(--text-color-muted, #59636e);
            font-size: 12px;
            font-weight: var(--font-weight-semibold, 600);
            white-space: nowrap;
        }
        .completed-label svg { width: 15px; fill: currentColor; }
        .empty-state {
            padding: 60px 24px;
            border: 1px dashed var(--border-color-default, #d0d7de);
            border-radius: 14px;
            color: var(--text-color-muted, #59636e);
            text-align: center;
        }
        .empty-state strong, .empty-state span { display: block; }
        .empty-state strong {
            color: var(--text-color-default, #1f2328);
            font-size: 16px;
        }
        .toast {
            position: fixed;
            right: 22px;
            bottom: 22px;
            z-index: 20;
            max-width: min(420px, calc(100vw - 44px));
            padding: 12px 14px;
            border: 1px solid var(--border-color-default, #d0d7de);
            border-radius: 10px;
            background: var(--background-color-default, #ffffff);
            box-shadow: 0 14px 42px color-mix(in srgb, var(--text-color-default, #1f2328) 18%, transparent);
            opacity: 0;
            pointer-events: none;
            transform: translateY(8px);
            transition: opacity .18s ease, transform .18s ease;
        }
        .toast-visible {
            opacity: 1;
            transform: translateY(0);
        }
        .toast-error {
            border-color: var(--true-color-red, #cf222e);
            color: var(--true-color-red, #cf222e);
        }
        [hidden] { display: none !important; }
        :focus-visible {
            outline: 2px solid var(--color-focus-outline, #0969da);
            outline-offset: 2px;
        }
        @media (max-width: 760px) {
            .shell { padding: 20px 16px 40px; }
            .hero { padding: 24px; }
            .toolbar { align-items: stretch; flex-wrap: wrap; }
            .tabs { flex: 1 0 auto; }
            .tab { flex: 1; }
            .search-wrap { order: 3; flex-basis: 100%; }
            .ideas-grid { grid-template-columns: 1fr; }
            .card-footer { align-items: stretch; flex-direction: column; }
            .launch-button { justify-content: center; }
        }
    </style>
</head>
<body>
    <main class="shell">
        <header class="hero">
            <div class="eyebrow">Project backlog</div>
            <h1>Ideas, ready for the next move.</h1>
            <p class="hero-copy">A live view of <code>IDEAS.md</code>. Ideas with a plan can move into implementation; ideas without one can start a dedicated planning session.</p>
            <div class="stats" aria-label="Idea summary">
                <div class="stat"><strong>${summary.active}</strong><span>Current ideas</span></div>
                <div class="stat"><strong>${summary.implementationReady}</strong><span>Ready to implement</span></div>
                <div class="stat"><strong>${summary.needsPlanning}</strong><span>Need planning</span></div>
                <div class="stat"><strong>${summary.archive}</strong><span>Archived</span></div>
            </div>
        </header>

        <div class="toolbar">
            <div class="tabs" role="tablist" aria-label="Idea sections">
                <button class="tab" id="tab-active" type="button" role="tab" data-tab="active" aria-controls="panel-active" aria-selected="${initialView === "active"}">Current <span>${summary.active}</span></button>
                <button class="tab" id="tab-archive" type="button" role="tab" data-tab="archive" aria-controls="panel-archive" aria-selected="${initialView === "archive"}">Archive <span>${summary.archive}</span></button>
            </div>
            <label class="search-wrap">
                <span hidden>Search ideas</span>
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11.5 10.5 15 14l-1 1-3.5-3.5a6 6 0 1 1 1-1ZM6.5 11a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Z"/></svg>
                <input class="search" type="search" placeholder="Search ideas..." autocomplete="off">
            </label>
            <button class="refresh" type="button" title="Reload IDEAS.md" aria-label="Reload IDEAS.md">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.6 2.4a.75.75 0 0 1 1 0 .75.75 0 0 1 0 1.1l-2 2A6 6 0 1 1 7.8 2a.75.75 0 0 1 .2 1.5 4.5 4.5 0 1 0 3.5 2.9H9a.75.75 0 0 1 0-1.5h4.3V.8a.75.75 0 0 1 1.5 0v1.6Z"/></svg>
            </button>
        </div>

        ${renderPanel("active", activeIdeas, initialView)}
        ${renderPanel("archive", archivedIdeas, initialView)}
    </main>
    <div class="toast" role="status" aria-live="polite"></div>

    <script>
        const canvasConfig = ${config};
        const tabs = [...document.querySelectorAll("[data-tab]")];
        const panels = [...document.querySelectorAll(".ideas-panel")];
        const searchInput = document.querySelector(".search");
        const toast = document.querySelector(".toast");
        let activeView = canvasConfig.initialView;
        let toastTimer;

        function setView(view) {
            activeView = view;
            for (const tab of tabs) {
                tab.setAttribute("aria-selected", String(tab.dataset.tab === view));
            }
            for (const panel of panels) {
                panel.hidden = panel.id !== "panel-" + view;
            }
            applyFilter();
        }

        function applyFilter() {
            const query = searchInput.value.trim().toLowerCase();
            const panel = document.querySelector("#panel-" + activeView);
            const cards = [...panel.querySelectorAll(".idea-card")];
            let visibleCount = 0;

            for (const card of cards) {
                const visible = !query || card.dataset.search.includes(query);
                card.hidden = !visible;
                visibleCount += visible ? 1 : 0;
            }
            panel.querySelector("[data-empty]").hidden = visibleCount !== 0;
        }

        function showToast(message, isError = false) {
            window.clearTimeout(toastTimer);
            toast.textContent = message;
            toast.classList.toggle("toast-error", isError);
            toast.classList.add("toast-visible");
            toastTimer = window.setTimeout(
                () => toast.classList.remove("toast-visible"),
                isError ? 7000 : 5000,
            );
        }

        for (const tab of tabs) {
            tab.addEventListener("click", () => setView(tab.dataset.tab));
        }
        searchInput.addEventListener("input", applyFilter);
        document.querySelector(".refresh").addEventListener(
            "click",
            () => window.location.reload(),
        );

        for (const button of document.querySelectorAll("[data-launch]")) {
            button.addEventListener("click", async () => {
                const originalHtml = button.innerHTML;
                button.disabled = true;
                button.setAttribute("aria-busy", "true");
                button.textContent = "Requesting session...";

                try {
                    const response = await fetch("/launch", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-Canvas-Token": canvasConfig.token,
                        },
                        body: JSON.stringify({
                            action: button.dataset.action,
                            ideaId: button.dataset.ideaId,
                        }),
                    });
                    const payload = await response.json();
                    if (!response.ok) {
                        throw new Error(payload.message || "Session request failed.");
                    }
                    button.removeAttribute("aria-busy");
                    button.textContent = "Session requested";
                    showToast(payload.message);
                } catch (error) {
                    button.disabled = false;
                    button.removeAttribute("aria-busy");
                    button.innerHTML = originalHtml;
                    showToast(error.message || "Session request failed.", true);
                }
            });
        }

        setView(activeView);
    </script>
</body>
</html>`;
}

export function renderErrorHtml(error) {
    const message = error instanceof Error ? error.message : String(error);
    return `<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Project ideas unavailable</title>
    <style>
        body {
            display: grid;
            min-height: 100vh;
            margin: 0;
            padding: 24px;
            place-items: center;
            background: var(--background-color-default, #ffffff);
            color: var(--text-color-default, #1f2328);
            font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
        }
        main {
            max-width: 560px;
            padding: 24px;
            border: 1px solid var(--true-color-red, #cf222e);
            border-radius: 12px;
        }
        h1 { margin-top: 0; }
        p { color: var(--text-color-muted, #59636e); }
        code { font-family: var(--font-mono, "SFMono-Regular", Consolas, monospace); }
    </style>
</head>
<body>
    <main>
        <h1>Could not load project ideas</h1>
        <p>Check that <code>IDEAS.md</code> exists and follows the expected heading structure.</p>
        <code>${escapeHtml(message)}</code>
    </main>
</body>
</html>`;
}
