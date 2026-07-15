import { css } from 'lit';

export const chatTranscriptStyles = css`
  .chat-col {
    display: flex;
    min-width: 0;
    min-height: 0;
    flex: 1;
    flex-direction: column;
    background: var(--bg);
  }

  .conversation-header {
    display: flex;
    min-height: 52px;
    align-items: center;
    padding: 0 18px;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
  }

  .conversation-title {
    margin: 0;
    overflow: hidden;
    color: var(--fg);
    font-size: 0.88rem;
    font-weight: 500;
    letter-spacing: -0.012em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .conversation-main {
    display: flex;
    min-height: 0;
    flex: 1;
    flex-direction: column;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 20px clamp(18px, 4vw, 52px) 24px;
    scrollbar-color: var(--border-strong) transparent;
    scrollbar-width: thin;
    scroll-padding-bottom: 22px;
  }

  .empty-state {
    width: min(360px, 100%);
    margin: auto;
    color: var(--fg-muted);
    text-align: center;
  }

  .empty-state-icon {
    display: inline-grid;
    width: 34px;
    height: 34px;
    place-items: center;
    margin-bottom: 11px;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--fg-muted);
    background: var(--card);
    font-size: 1rem;
  }

  .empty-state h2 {
    margin: 0;
    color: var(--fg);
    font-size: 1rem;
    font-weight: 550;
    letter-spacing: -0.018em;
  }

  .empty-state p {
    margin: 7px 0 0;
    color: var(--fg-muted);
    font-size: 0.82rem;
    line-height: 1.5;
  }

  .msg {
    width: min(780px, 100%);
    margin: 0 auto;
    padding: 18px 0;
  }

  .msg + .msg {
    border-top: 1px solid var(--border);
  }

  .message-marker {
    display: grid;
    width: 24px;
    height: 24px;
    flex: 0 0 24px;
    place-items: center;
    border: 1px solid var(--border);
    border-radius: 50%;
    color: var(--fg-muted);
    background: var(--card);
  }

  .msg.assistant .message-marker {
    color: var(--accent);
    border-color: var(--accent-border);
    background: var(--accent-soft);
  }

  .message-marker .material-symbols-outlined {
    font-size: 0.8rem;
  }

  .message-content {
    min-width: 0;
    margin-top: 9px;
  }

  .message-heading {
    display: flex;
    min-height: 24px;
    align-items: center;
    gap: 7px;
  }

  .message-role {
    color: var(--fg);
    font-size: 0.78rem;
    font-weight: 600;
  }

  .message-time {
    color: var(--fg-muted);
    font-size: 0.78rem;
    font-weight: 400;
  }

  .response-state {
    display: inline-flex;
    margin-left: 2px;
    align-items: center;
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.58rem;
    gap: 5px;
  }

  .response-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent);
    animation: response-pulse 900ms ease-in-out infinite;
  }

  .message-body {
    color: var(--fg);
    font-size: 0.88rem;
    line-height: 1.62;
    overflow-wrap: anywhere;
  }

  .message-pending {
    color: var(--fg-muted);
  }

  .message-body > :first-child {
    margin-top: 0;
  }

  .message-body > :last-child {
    margin-bottom: 0;
  }

  .message-body p,
  .message-body ul,
  .message-body ol {
    margin: 0.55em 0;
  }

  .message-body ul,
  .message-body ol {
    padding-left: 1.25em;
  }

  .message-body a {
    color: var(--accent);
    font-weight: 500;
    text-underline-offset: 2px;
  }

  .message-body .inline-citation {
    position: relative;
    top: -0.18em;
    margin-left: 0.12em;
    font-size: 0.72em;
    line-height: 0;
  }

  .message-body .inline-citation a {
    font-weight: 600;
    text-decoration: none;
  }

  .message-body .inline-citation a::before {
    content: '[';
  }

  .message-body .inline-citation a::after {
    content: ']';
  }

  .message-body code {
    padding: 0.12em 0.32em;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--surface-muted);
    font-family: var(--font-mono);
    font-size: 0.84em;
  }

  .message-body pre {
    max-width: 100%;
    padding: 11px;
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--card);
  }

  .message-body pre code {
    padding: 0;
    border: 0;
    background: transparent;
  }

  .surfaces {
    display: flex;
    margin-top: 11px;
    flex-direction: column;
    gap: 8px;
  }

  .message-tools,
  .message-sources {
    display: flex;
    margin-top: 10px;
    align-items: center;
    color: var(--fg-muted);
    font-size: 0.68rem;
    line-height: 1.4;
    gap: 6px;
  }

  .message-tools > .material-symbols-outlined {
    font-size: 0.82rem;
  }

  .message-tools-label,
  .message-sources-label {
    font-weight: 600;
  }

  .message-tool-names {
    display: inline-flex;
    min-width: 0;
    flex-wrap: wrap;
    gap: 5px;
  }

  .message-tool-names span + span::before {
    margin-right: 5px;
    content: '·';
  }

  .message-sources {
    align-items: flex-start;
  }

  .message-sources-label {
    padding-top: 3px;
  }

  .message-source-links {
    display: flex;
    min-width: 0;
    flex-wrap: wrap;
    gap: 4px 12px;
  }

  .message-source {
    display: inline-flex;
    min-width: 0;
    align-items: center;
    border-radius: 3px;
    color: var(--fg-muted);
    font-size: 0.7rem;
    gap: 4px;
    line-height: 1.35;
    text-decoration: none;
  }

  a.message-source {
    text-decoration: underline;
    text-decoration-color: var(--border-strong);
    text-underline-offset: 2px;
  }

  a.message-source:hover {
    color: var(--accent);
    text-decoration-color: currentColor;
  }

  .message-source .material-symbols-outlined {
    flex: 0 0 auto;
    font-size: 0.82rem;
  }

  .message-source > span:last-child {
    max-width: 240px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .message-footer {
    display: flex;
    min-height: 28px;
    margin-top: 8px;
    align-items: center;
    color: var(--fg-muted);
    gap: 8px;
  }

  .message-token-count {
    font-size: 0.68rem;
  }

  .message-actions {
    display: inline-flex;
    align-items: center;
    gap: 1px;
  }

  .message-action {
    display: inline-grid;
    width: 28px;
    height: 28px;
    padding: 0;
    place-items: center;
    border: 0;
    border-radius: 5px;
    color: var(--fg-muted);
    background: transparent;
    cursor: pointer;
  }

  .message-action:hover,
  .message-action.active {
    color: var(--fg);
    background: var(--surface-muted);
  }

  .message-action.active {
    color: var(--accent);
  }

  .message-action.active .material-symbols-outlined {
    font-variation-settings: 'FILL' 1;
  }

  .message-action .material-symbols-outlined {
    font-size: 0.88rem;
  }

  .error-message {
    display: flex;
    margin-top: 9px;
    align-items: flex-start;
    padding: 8px 10px;
    border: 1px solid var(--danger-border);
    border-radius: 6px;
    color: var(--danger);
    background: var(--danger-soft);
    font-size: 0.74rem;
    line-height: 1.4;
    gap: 6px;
  }

  .error-message .material-symbols-outlined {
    margin-top: 1px;
    font-size: 0.95rem;
  }
`;
