import { css } from 'lit';

export const navigationStyles = css`
  .app-header {
    position: relative;
    z-index: 40;
    display: flex;
    min-height: 52px;
    align-items: center;
    padding: 0 8px;
    border-bottom: 1px solid var(--border);
    background: var(--card);
  }

  .brand {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: 8px;
  }

  .brand-mark {
    position: relative;
    display: grid;
    width: 18px;
    height: 26px;
    flex: 0 0 18px;
    place-items: center;
  }

  .brand-mark::before {
    position: absolute;
    top: 3px;
    bottom: 3px;
    left: 8px;
    width: 1px;
    content: '';
    background: var(--accent);
  }

  .brand-mark span {
    position: relative;
    z-index: 1;
    display: block;
    width: 5px;
    height: 5px;
    border: 1px solid var(--card);
    border-radius: 50%;
    background: var(--accent);
  }

  .brand-mark span:nth-child(2) {
    background: var(--signal);
  }

  .brand-name {
    overflow: hidden;
    color: var(--fg);
    font-size: 0.94rem;
    font-weight: 600;
    letter-spacing: -0.018em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .header-actions {
    display: flex;
    align-items: center;
    margin-left: auto;
    gap: 1px;
  }

  .header-separator {
    width: 1px;
    height: 18px;
    margin: 0 5px;
    background: var(--border);
  }

  .icon-button,
  .header-button,
  .rail-action,
  .send-button,
  .primary-button,
  .secondary-button,
  .danger-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 0;
    cursor: pointer;
  }

  .icon-button,
  .header-button {
    width: 34px;
    height: 34px;
    padding: 0;
    border-radius: 6px;
    color: var(--fg-muted);
    background: transparent;
    text-decoration: none;
    transition:
      color 120ms ease,
      background-color 120ms ease;
  }

  .icon-button:hover,
  .header-button:hover,
  .header-button[aria-expanded='true'] {
    color: var(--fg);
    background: var(--surface-muted);
  }

  .icon-button:disabled {
    cursor: default;
    opacity: 0.45;
  }

  .material-symbols-outlined {
    font-family: 'Material Symbols Outlined';
    font-size: 1.08rem;
    font-style: normal;
    font-weight: normal;
    line-height: 1;
    vertical-align: middle;
  }

  .github-icon {
    width: 17px;
    height: 17px;
    fill: currentColor;
  }

  .body {
    display: flex;
    min-height: 0;
    flex: 1;
  }

  .thread-rail,
  .memory-rail {
    display: flex;
    min-height: 0;
    flex: 0 0 auto;
    flex-direction: column;
    overflow: hidden;
    background: var(--card);
  }

  .thread-rail {
    width: 252px;
    border-right: 1px solid var(--border);
    transition: width 160ms ease;
  }

  .memory-rail {
    width: 300px;
    border-left: 1px solid var(--border);
  }

  .memory-rail.collapsed {
    display: none;
  }

  @media (min-width: 901px) {
    .thread-rail.collapsed {
      display: flex;
      width: 52px;
    }

    .thread-rail.collapsed .rail-header,
    .thread-rail.collapsed .rail-section-label,
    .thread-rail.collapsed .thread-list,
    .thread-rail.collapsed .identity-label,
    .thread-rail.collapsed .identity-control,
    .thread-rail.collapsed .profile-button {
      display: none;
    }

    .thread-rail.collapsed .rail-content {
      padding: 8px;
      overflow: hidden;
    }

    .thread-rail.collapsed .rail-actions {
      flex-direction: column;
      margin: 0;
      gap: 4px;
    }

    .thread-rail.collapsed .identity-card {
      padding: 8px 10px;
    }

    .thread-rail.collapsed .identity-row {
      flex-direction: column;
      gap: 5px;
    }
  }

  .rail-header {
    display: flex;
    min-height: 52px;
    align-items: center;
    padding: 0 9px 0 12px;
    border-bottom: 1px solid var(--border);
    gap: 7px;
  }

  .rail-heading {
    min-width: 0;
    flex: 1;
  }

  .rail-title {
    margin: 0;
    overflow: hidden;
    color: var(--fg);
    font-size: 0.86rem;
    font-weight: 600;
    letter-spacing: -0.012em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .rail-eyebrow,
  .rail-section-label,
  .identity-label,
  .field-label {
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.61rem;
    font-weight: 500;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  .rail-eyebrow {
    margin: 0 0 3px;
  }

  .rail-count {
    display: inline-flex;
    min-width: 23px;
    height: 21px;
    align-items: center;
    justify-content: center;
    padding: 0 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--fg-muted);
    background: var(--bg);
    font-family: var(--font-mono);
    font-size: 0.59rem;
  }

  .rail-content {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 10px;
    scrollbar-color: var(--border-strong) transparent;
    scrollbar-width: thin;
  }

  .rail-actions {
    display: flex;
    align-items: center;
    margin-bottom: 15px;
    gap: 4px;
  }

  .rail-action {
    width: 34px;
    height: 34px;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 7px;
    color: var(--fg-muted);
    background: var(--card);
    transition:
      color 120ms ease,
      border-color 120ms ease,
      background-color 120ms ease;
  }

  .rail-action:hover {
    border-color: var(--border-strong);
    color: var(--fg);
    background: var(--surface-muted);
  }

  .rail-section-label {
    display: flex;
    align-items: center;
    margin: 0 4px 7px;
  }

  .rail-section-label span:last-child {
    margin-left: auto;
  }

  .thread-list,
  .memory-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .thread-row,
  .memory-row {
    position: relative;
    display: flex;
    min-width: 0;
    align-items: center;
    border-radius: 6px;
    transition: background-color 100ms ease;
  }

  .thread-row:hover,
  .memory-row:hover {
    background: var(--surface-muted);
  }

  .thread-row.active,
  .memory-row.active {
    background: var(--accent-soft);
  }

  .thread-main,
  .memory-main {
    display: flex;
    min-width: 0;
    flex: 1;
    flex-direction: column;
    align-items: flex-start;
    padding: 8px 7px;
    border: 0;
    border-radius: 6px;
    color: inherit;
    background: transparent;
    cursor: pointer;
    text-align: left;
  }

  .row-title {
    width: 100%;
    overflow: hidden;
    color: var(--fg);
    font-size: 0.78rem;
    font-weight: 500;
    line-height: 1.35;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .row-meta {
    width: 100%;
    margin-top: 2px;
    overflow: hidden;
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.58rem;
    line-height: 1.4;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .row-actions {
    display: flex;
    flex: 0 0 auto;
    align-items: center;
    padding-right: 3px;
    opacity: 0;
    transition: opacity 100ms ease;
  }

  .thread-row:hover .row-actions,
  .thread-row:focus-within .row-actions,
  .memory-row:hover .row-actions,
  .memory-row:focus-within .row-actions,
  .thread-row.active .row-actions,
  .memory-row.active .row-actions {
    opacity: 1;
  }

  .row-actions .icon-button {
    width: 29px;
    height: 29px;
  }

  .row-actions .material-symbols-outlined {
    font-size: 0.95rem;
  }

  .empty-list {
    padding: 8px 4px;
    color: var(--fg-muted);
    font-size: 0.76rem;
    line-height: 1.5;
  }

  .identity-card {
    padding: 9px 10px 10px;
    border-top: 1px solid var(--border);
    background: var(--card);
  }

  .identity-label {
    display: block;
    margin: 0 0 6px 2px;
  }

  .identity-row {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: 8px;
  }

  .avatar {
    display: grid;
    width: 30px;
    height: 30px;
    flex: 0 0 30px;
    place-items: center;
    border: 1px solid var(--border);
    border-radius: 50%;
    color: var(--fg);
    background: var(--surface-muted);
    font-family: var(--font-mono);
    font-size: 0.61rem;
    font-weight: 600;
  }

  .identity-control {
    min-width: 0;
    flex: 1;
  }

  .identity-name {
    overflow: hidden;
    color: var(--fg);
    font-size: 0.76rem;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .identity-select {
    width: 100%;
    margin-top: 1px;
    padding: 0;
    border: 0;
    color: var(--fg-muted);
    background: transparent;
    font-family: var(--font-mono);
    font-size: 0.58rem;
    cursor: pointer;
  }

  .profile-button {
    flex: 0 0 auto;
  }

  .sign-out-button {
    flex: 0 0 auto;
  }
`;
