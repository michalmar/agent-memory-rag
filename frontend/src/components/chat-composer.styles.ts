import { css } from 'lit';

export const chatComposerStyles = css`
  .composer {
    flex: 0 0 auto;
    padding: 0 clamp(18px, 4vw, 52px) max(18px, env(safe-area-inset-bottom));
    background: transparent;
  }

  .composer-inner {
    width: min(820px, 100%);
    margin: 0 auto;
  }

  .composer-box {
    position: relative;
    display: flex;
    min-height: 142px;
    flex-direction: column;
    padding: 12px;
    border: 1px solid var(--border-strong);
    border-radius: 12px;
    background: transparent;
    box-shadow: var(--shadow-sm);
    gap: 5px;
    transition:
      border-color 120ms ease,
      box-shadow 120ms ease;
  }

  .composer-box:focus-within {
    border-color: var(--accent-border);
    box-shadow: 0 0 0 2px var(--accent-ring);
  }

  .composer-box textarea {
    width: 100%;
    min-width: 0;
    min-height: 76px;
    max-height: min(240px, 38dvh);
    overflow-x: hidden;
    overflow-y: hidden;
    resize: none;
    padding: 3px 5px 8px;
    border: 0;
    outline: 0;
    color: var(--fg);
    background: transparent;
    font-size: 0.9rem;
    line-height: 1.5;
  }

  .composer-box textarea::placeholder,
  .memory-search-field input::placeholder {
    color: var(--placeholder);
  }

  .composer-toolbar {
    display: flex;
    min-width: 0;
    min-height: 36px;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }

  .composer-selectors {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: 2px;
  }

  .composer-selector {
    display: inline-flex;
    min-width: 0;
    height: 32px;
    align-items: center;
    padding: 0 4px;
    border: 0;
    border-radius: 6px;
    color: var(--fg-muted);
    background: transparent;
    gap: 3px;
    transition:
      color 120ms ease,
      background-color 120ms ease;
  }

  .composer-selector:hover {
    color: var(--fg);
    background: var(--surface-muted);
  }

  .selector-leading {
    flex: 0 0 auto;
    font-size: 0.92rem;
  }

  .selector-chevron {
    flex: 0 0 auto;
    font-size: 0.78rem;
    pointer-events: none;
  }

  .composer-select {
    min-width: 0;
    height: 30px;
    padding: 0;
    border: 0;
    outline-offset: -1px;
    color: currentColor;
    appearance: none;
    background: transparent;
    cursor: pointer;
    font-size: 0.74rem;
    font-weight: 500;
    line-height: 1;
    text-overflow: ellipsis;
  }

  .composer-select:disabled {
    color: var(--fg-muted);
    cursor: default;
  }

  .composer-select option {
    color: var(--fg);
    background: var(--card);
  }

  .agent-select {
    max-width: 174px;
  }

  .model-select {
    max-width: 132px;
  }

  .model-selector {
    color: var(--fg-muted);
  }

  .send-button {
    width: 36px;
    height: 36px;
    flex: 0 0 36px;
    padding: 0;
    border-radius: 50%;
    color: var(--accent-fg);
    background: var(--accent);
    box-shadow: var(--shadow-button);
    transition:
      background-color 120ms ease,
      transform 120ms ease,
      opacity 120ms ease;
  }

  .send-button:hover:not(:disabled) {
    background: var(--accent-hover);
    transform: translateY(-1px);
  }

  .send-button.stop-button {
    color: var(--fg);
    background: var(--surface-muted);
    box-shadow: inset 0 0 0 1px var(--border-strong);
  }

  .send-button.stop-button:hover {
    color: var(--accent);
    background: var(--card);
  }

  .send-button:disabled {
    cursor: not-allowed;
    opacity: 0.38;
    box-shadow: none;
  }
`;
