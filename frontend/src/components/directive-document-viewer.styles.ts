import { css } from 'lit';

export const directiveDocumentViewerStyles = css`
  .document-viewer-scrim {
    position: fixed;
    z-index: 60;
    border: 0;
    background: var(--scrim);
    cursor: default;
    inset: 0;
  }

  .document-viewer {
    position: fixed;
    z-index: 61;
    top: 0;
    right: 0;
    display: flex;
    width: min(860px, 96vw);
    height: 100vh;
    height: 100dvh;
    flex-direction: column;
    border-left: 1px solid var(--border);
    background: var(--card);
    box-shadow: var(--shadow-drawer);
  }

  .document-focus-guard {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    opacity: 0;
    pointer-events: none;
  }

  .document-viewer-header {
    display: flex;
    min-height: 76px;
    align-items: flex-start;
    padding: 13px 10px 11px 20px;
    border-bottom: 1px solid var(--border);
    gap: 12px;
  }

  .document-viewer-heading {
    min-width: 0;
    flex: 1;
  }

  .document-viewer-heading .rail-eyebrow {
    margin-bottom: 4px;
  }

  .document-viewer-heading h2 {
    margin: 0;
    overflow: hidden;
    color: var(--fg);
    font-size: 1rem;
    font-weight: 600;
    letter-spacing: -0.018em;
    line-height: 1.3;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .document-viewer-metadata {
    display: flex;
    margin: 5px 0 0;
    flex-wrap: wrap;
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.62rem;
    gap: 6px 12px;
  }

  .document-viewer-tabs {
    display: flex;
    min-height: 42px;
    align-items: end;
    padding: 0 20px;
    border-bottom: 1px solid var(--border);
    background: var(--card);
    gap: 18px;
  }

  .document-viewer-tab {
    height: 42px;
    padding: 0 1px;
    border: 0;
    border-bottom: 2px solid transparent;
    color: var(--fg-muted);
    background: transparent;
    cursor: pointer;
    font-size: 0.76rem;
    font-weight: 550;
  }

  .document-viewer-tab[aria-selected='true'] {
    border-bottom-color: var(--accent);
    color: var(--fg);
  }

  .document-viewer-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    background: var(--bg);
    overscroll-behavior: contain;
  }

  .document-viewer-state {
    display: flex;
    min-height: 220px;
    align-items: center;
    justify-content: center;
    padding: 30px;
    color: var(--fg-muted);
    font-size: 0.8rem;
    gap: 9px;
    text-align: center;
  }

  .document-viewer-spinner {
    width: 14px;
    height: 14px;
    border: 2px solid var(--border-strong);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: document-viewer-spin 700ms linear infinite;
  }

  .document-viewer-error {
    flex-direction: column;
  }

  .document-viewer-error > .material-symbols-outlined {
    color: var(--danger);
  }

  .document-viewer-error p {
    max-width: 440px;
    margin: 0;
    line-height: 1.5;
  }

  .document-markdown {
    width: min(720px, 100%);
    padding: 30px clamp(20px, 5vw, 48px) 54px;
    margin: 0 auto;
    color: var(--fg);
    font-size: 0.9rem;
    line-height: 1.68;
    overflow-wrap: anywhere;
  }

  .document-markdown > :first-child {
    margin-top: 0;
  }

  .document-markdown > :last-child {
    margin-bottom: 0;
  }

  .document-markdown h1,
  .document-markdown h2,
  .document-markdown h3,
  .document-markdown h4 {
    margin: 1.65em 0 0.55em;
    color: var(--fg);
    letter-spacing: -0.025em;
    line-height: 1.25;
  }

  .document-markdown h1 {
    padding-bottom: 0.35em;
    border-bottom: 1px solid var(--border);
    font-size: 1.55rem;
  }

  .document-markdown h2 {
    font-size: 1.22rem;
  }

  .document-markdown h3 {
    font-size: 1.02rem;
  }

  .document-markdown p,
  .document-markdown ul,
  .document-markdown ol,
  .document-markdown blockquote {
    margin: 0.7em 0;
  }

  .document-markdown ul,
  .document-markdown ol {
    padding-left: 1.45em;
  }

  .document-markdown a {
    color: var(--accent);
    font-weight: 500;
    text-underline-offset: 2px;
  }

  .document-markdown-link {
    display: inline;
    padding: 0;
    border: 0;
    color: var(--accent);
    background: transparent;
    cursor: pointer;
    font: inherit;
    font-weight: 500;
    text-align: left;
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  .document-markdown blockquote {
    padding: 0.15em 0 0.15em 1em;
    border-left: 3px solid var(--accent-border);
    color: var(--fg-muted);
  }

  .document-markdown code {
    padding: 0.12em 0.32em;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--surface-muted);
    font-family: var(--font-mono);
    font-size: 0.84em;
  }

  .document-markdown pre {
    max-width: 100%;
    padding: 13px;
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--card);
  }

  .document-markdown pre code {
    padding: 0;
    border: 0;
    background: transparent;
  }

  .document-markdown table {
    display: block;
    width: max-content;
    max-width: 100%;
    margin: 1em 0;
    overflow-x: auto;
    border-collapse: collapse;
  }

  .document-markdown th,
  .document-markdown td {
    min-width: 120px;
    padding: 8px 10px;
    border: 1px solid var(--border);
    text-align: left;
    vertical-align: top;
  }

  .document-markdown th {
    background: var(--surface-muted);
    font-weight: 600;
  }

  .document-markdown img {
    max-width: 100%;
    height: auto;
  }

  .document-pdf {
    display: flex;
    height: 100%;
    min-height: 0;
    flex-direction: column;
    padding: 14px;
    gap: 10px;
  }

  .document-pdf-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
  }

  .document-pdf-actions a {
    text-decoration: none;
  }

  .document-pdf-frame {
    width: 100%;
    min-height: 0;
    flex: 1;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--card);
  }

  .document-pdf-fallback {
    margin: 0;
    color: var(--fg-muted);
    font-size: 0.68rem;
    line-height: 1.45;
  }

  @keyframes document-viewer-spin {
    to {
      transform: rotate(360deg);
    }
  }
`;
