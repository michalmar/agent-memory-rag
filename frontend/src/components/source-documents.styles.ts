import { css } from 'lit';

export const sourceDocumentsStyles = css`
  .source-upload {
    display: flex;
    flex-direction: column;
    margin-bottom: 16px;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--bg);
    gap: 10px;
  }

  .source-upload-note,
  .source-delete-confirmation p,
  .source-notice {
    margin: 0;
    color: var(--fg-muted);
    font-size: 0.72rem;
    line-height: 1.5;
  }

  .source-upload-note code {
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.64rem;
  }

  .source-upload-button {
    align-self: flex-start;
  }

  .source-upload-button .material-symbols-outlined {
    font-size: 1rem;
  }

  .source-progress {
    width: 100%;
    height: 5px;
    overflow: hidden;
    border-radius: 999px;
    background: var(--border);
  }

  .source-progress span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--accent);
    transition: width 100ms ease;
  }

  .source-error {
    margin: 0;
    color: var(--danger);
    font-size: 0.7rem;
    line-height: 1.4;
  }

  .source-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .source-entry {
    border-radius: 6px;
  }

  .source-row {
    display: flex;
    min-width: 0;
    align-items: center;
    border-radius: 6px;
  }

  .source-row:hover,
  .source-entry:focus-within .source-row {
    background: var(--surface-muted);
  }

  .source-row:hover .row-actions,
  .source-entry:focus-within .row-actions {
    opacity: 1;
  }

  .source-entry .row-actions {
    opacity: 0.65;
  }

  .source-main {
    display: flex;
    min-width: 0;
    flex: 1;
    flex-direction: column;
    padding: 8px 7px;
  }

  .source-delete-confirmation {
    display: flex;
    flex-direction: column;
    margin: 2px 0 8px;
    padding: 10px;
    border: 1px solid var(--danger-border);
    border-radius: 6px;
    background: var(--danger-soft);
    gap: 8px;
  }

  .source-delete-confirmation strong {
    color: var(--fg);
    overflow-wrap: anywhere;
  }

  .source-delete-actions {
    display: flex;
    gap: 6px;
  }

  .source-load-more {
    width: 100%;
    margin-top: 10px;
  }
`;
