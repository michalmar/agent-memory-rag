import { css } from 'lit';

export const memoryProfileStyles = css`
  .memory-search {
    display: flex;
    margin-bottom: 13px;
    align-items: center;
    gap: 4px;
  }

  .memory-search-field {
    position: relative;
    min-width: 0;
    flex: 1;
  }

  .memory-search-field .material-symbols-outlined {
    position: absolute;
    top: 50%;
    left: 10px;
    color: var(--fg-muted);
    font-size: 0.95rem;
    pointer-events: none;
    transform: translateY(-50%);
  }

  .memory-search-field input {
    width: 100%;
    min-height: 38px;
    padding: 0 9px 0 32px;
    border: 1px solid var(--border);
    border-radius: 7px;
    color: var(--fg);
    background: var(--bg);
    font-size: 0.76rem;
  }

  .similarity {
    display: inline-flex;
    min-height: 22px;
    align-items: center;
    margin-right: 3px;
    padding: 0 6px;
    border-radius: 999px;
    color: var(--signal);
    background: var(--signal-soft);
    font-family: var(--font-mono);
    font-size: 0.58rem;
    font-weight: 600;
  }

  .memory-detail {
    padding: 2px 2px 16px;
  }

  .memory-detail-back {
    margin: 0 0 12px -6px;
  }

  .memory-detail h2 {
    margin: 4px 0 0;
    color: var(--fg);
    font-size: 0.98rem;
    font-weight: 600;
    letter-spacing: -0.015em;
    line-height: 1.25;
  }

  .memory-detail-meta {
    display: flex;
    flex-wrap: wrap;
    margin: 8px 0 0;
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.58rem;
    gap: 5px 8px;
  }

  .memory-summary {
    margin: 16px 0 0;
    padding: 13px 0;
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    color: var(--fg);
    font-size: 0.8rem;
    line-height: 1.6;
    white-space: pre-wrap;
  }

  .memory-detail-actions {
    display: flex;
    margin-top: 12px;
    gap: 6px;
  }

  .primary-button,
  .secondary-button,
  .danger-button {
    min-height: 36px;
    padding: 0 11px;
    border-radius: 7px;
    font-size: 0.74rem;
    font-weight: 550;
    gap: 5px;
  }

  .primary-button {
    color: var(--accent-fg);
    background: var(--accent);
  }

  .secondary-button,
  .danger-button {
    border: 1px solid var(--border);
    background: var(--card);
  }

  .secondary-button:hover {
    border-color: var(--border-strong);
    background: var(--surface-muted);
  }

  .danger-button {
    color: var(--danger);
  }

  .danger-button:hover {
    border-color: var(--danger-border);
    background: var(--danger-soft);
  }

  .panel-scrim {
    position: fixed;
    z-index: 28;
    display: none;
    border: 0;
    background: var(--scrim);
    cursor: default;
    inset: 52px 0 0;
  }

  .drawer-scrim {
    position: fixed;
    z-index: 50;
    border: 0;
    background: var(--scrim);
    inset: 0;
  }

  .profile-drawer {
    position: fixed;
    z-index: 51;
    top: 0;
    right: 0;
    display: flex;
    width: min(450px, 94vw);
    height: 100vh;
    height: 100dvh;
    flex-direction: column;
    border-left: 1px solid var(--border);
    background: var(--card);
    box-shadow: var(--shadow-drawer);
  }

  .drawer-header {
    display: flex;
    min-height: 52px;
    align-items: center;
    padding: 0 9px 0 16px;
    border-bottom: 1px solid var(--border);
    gap: 8px;
  }

  .drawer-heading {
    min-width: 0;
    flex: 1;
  }

  .drawer-heading h2 {
    margin: 0;
    color: var(--fg);
    font-size: 0.88rem;
    font-weight: 600;
    letter-spacing: -0.012em;
  }

  .drawer-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 16px;
  }

  .field-note {
    margin: 0 0 14px;
    color: var(--fg-muted);
    font-size: 0.78rem;
    line-height: 1.5;
  }

  .field-label {
    display: block;
    margin: 0 0 6px 2px;
  }

  .profile-editor {
    width: 100%;
    min-height: 410px;
    resize: vertical;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 7px;
    color: var(--fg);
    background: var(--bg);
    font-family: var(--font-mono);
    font-size: 0.68rem;
    line-height: 1.52;
    tab-size: 2;
  }

  .drawer-actions {
    display: flex;
    align-items: center;
    padding: 10px 16px max(10px, env(safe-area-inset-bottom));
    border-top: 1px solid var(--border);
    background: var(--card);
    gap: 7px;
  }

  .drawer-actions .danger-button {
    margin-left: auto;
  }

  .toast {
    position: fixed;
    z-index: 70;
    bottom: 18px;
    left: 50%;
    max-width: min(400px, calc(100vw - 24px));
    padding: 9px 12px;
    border: 1px solid var(--border-strong);
    border-radius: 7px;
    color: var(--fg);
    background: var(--card);
    box-shadow: var(--shadow-drawer);
    font-size: 0.76rem;
    font-weight: 500;
    line-height: 1.35;
    text-align: center;
    transform: translateX(-50%);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    overflow: hidden;
    border: 0;
    margin: -1px;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }
`;
