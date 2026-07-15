import { css } from 'lit';

export const baseStyles = css`
  :host {
    display: block;
    height: 100vh;
    height: 100dvh;
    min-height: 0;
    overflow: hidden;
    color: var(--fg);
    background: var(--bg);
    font-family: var(--font-body);
    font-size: 16px;
  }

  conversation-sidebar,
  memory-rail,
  chat-transcript,
  chat-composer,
  profile-drawer {
    display: contents;
  }

  *,
  *::before,
  *::after {
    box-sizing: border-box;
  }

  button,
  input,
  select,
  textarea {
    font: inherit;
  }

  button,
  select,
  a {
    color: inherit;
  }

  button,
  a {
    -webkit-tap-highlight-color: transparent;
  }

  button:focus-visible,
  a:focus-visible,
  input:focus-visible,
  select:focus-visible,
  textarea:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .send-button:focus-visible,
  .primary-button:focus-visible {
    outline: 2px solid var(--accent-fg);
    outline-offset: -3px;
  }

  .app-shell {
    display: flex;
    height: 100%;
    min-height: 0;
    flex-direction: column;
    background: var(--bg);
  }

  .auth-shell {
    position: relative;
    display: grid;
    height: 100%;
    min-height: 0;
    padding: 24px;
    overflow: auto;
    background:
      linear-gradient(var(--border) 1px, transparent 1px),
      linear-gradient(90deg, var(--border) 1px, transparent 1px),
      var(--bg);
    background-position: center;
    background-size: 48px 48px;
    place-items: center;
  }

  .auth-shell::before {
    position: absolute;
    background: radial-gradient(circle, var(--accent-ring) 0, transparent 68%);
    content: '';
    inset: 0;
    pointer-events: none;
  }

  .auth-theme-toggle {
    position: fixed;
    z-index: 2;
    top: 16px;
    right: 16px;
    display: inline-flex;
    width: 36px;
    height: 36px;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--fg-muted);
    background: var(--card);
    box-shadow: var(--shadow-sm);
    cursor: pointer;
  }

  .auth-theme-toggle:hover {
    color: var(--fg);
    border-color: var(--border-strong);
  }

  .auth-card {
    position: relative;
    z-index: 1;
    width: min(100%, 440px);
    padding: 38px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--card);
    box-shadow: var(--shadow-drawer);
  }

  .auth-brand {
    display: flex;
    align-items: center;
    gap: 9px;
  }

  .auth-heading {
    margin: 42px 0 28px;
  }

  .auth-eyebrow {
    margin: 0 0 10px;
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .auth-heading h1 {
    margin: 0;
    color: var(--fg);
    font-size: clamp(1.75rem, 5vw, 2.25rem);
    font-weight: 600;
    letter-spacing: -0.045em;
    line-height: 1.08;
  }

  .auth-heading p:last-child {
    margin: 15px 0 0;
    color: var(--fg-muted);
    font-size: 0.88rem;
    line-height: 1.65;
  }

  .auth-progress,
  .auth-error {
    display: flex;
    align-items: center;
    padding: 11px 12px;
    border-radius: 8px;
    font-size: 0.78rem;
    line-height: 1.45;
    gap: 9px;
  }

  .auth-progress {
    border: 1px solid var(--border);
    color: var(--fg-muted);
    background: var(--surface-muted);
  }

  .auth-error {
    margin-bottom: 12px;
    border: 1px solid var(--danger-border);
    color: var(--danger);
    background: var(--danger-soft);
  }

  .auth-error .material-symbols-outlined {
    flex: 0 0 auto;
  }

  .auth-spinner {
    width: 15px;
    height: 15px;
    flex: 0 0 auto;
    border: 2px solid var(--border-strong);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: auth-spin 700ms linear infinite;
  }

  .entra-sign-in {
    display: flex;
    width: 100%;
    min-height: 46px;
    align-items: center;
    justify-content: center;
    padding: 0 16px;
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    color: var(--fg);
    background: var(--bg-alt);
    box-shadow: var(--shadow-sm);
    font-size: 0.82rem;
    font-weight: 550;
    cursor: pointer;
    gap: 11px;
    transition:
      border-color 120ms ease,
      background-color 120ms ease,
      transform 120ms ease;
  }

  .entra-sign-in:hover:not(:disabled) {
    border-color: var(--accent);
    background: var(--accent-soft);
    transform: translateY(-1px);
  }

  .entra-sign-in:disabled {
    cursor: wait;
    opacity: 0.65;
  }

  .microsoft-mark {
    display: grid;
    width: 17px;
    height: 17px;
    flex: 0 0 17px;
    grid-template-columns: repeat(2, 1fr);
    gap: 1.5px;
  }

  .microsoft-mark span:nth-child(1) { background: #f35325; }
  .microsoft-mark span:nth-child(2) { background: #81bc06; }
  .microsoft-mark span:nth-child(3) { background: #05a6f0; }
  .microsoft-mark span:nth-child(4) { background: #ffba08; }

  .auth-footnote {
    margin: 24px 0 0;
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.62rem;
    line-height: 1.5;
    text-align: center;
  }
`;
