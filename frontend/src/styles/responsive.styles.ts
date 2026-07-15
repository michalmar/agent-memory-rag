import { css } from 'lit';

export const responsiveStyles = css`
  @keyframes response-pulse {
    0%,
    100% {
      opacity: 0.35;
    }

    50% {
      opacity: 1;
    }
  }

  @keyframes auth-spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 1360px) {
    .memory-rail,
    .memory-rail.collapsed {
      position: fixed;
      z-index: 31;
      top: 52px;
      right: 0;
      bottom: 0;
      display: flex;
      width: min(340px, 94vw);
      border-left: 1px solid var(--border);
      box-shadow: var(--shadow-drawer);
      transform: translateX(100%);
      transition:
        transform 160ms ease,
        visibility 160ms ease;
      visibility: hidden;
    }

    .memory-rail:not(.collapsed) {
      transform: translateX(0);
      visibility: visible;
    }

    .memory-scrim {
      display: block;
    }
  }

  @media (max-width: 900px) {
    .thread-rail,
    .thread-rail.collapsed {
      position: fixed;
      z-index: 31;
      top: 52px;
      bottom: 0;
      left: 0;
      display: flex;
      width: min(300px, 92vw);
      border-right: 1px solid var(--border);
      box-shadow: var(--shadow-drawer);
      transform: translateX(-100%);
      transition:
        transform 160ms ease,
        visibility 160ms ease;
      visibility: hidden;
    }

    .thread-rail:not(.collapsed) {
      transform: translateX(0);
      visibility: visible;
    }

    .thread-scrim {
      display: block;
    }
  }

  @media (max-width: 680px) {
    .app-header {
      padding: 0 5px;
    }

    .icon-button,
    .header-button {
      width: 32px;
      height: 32px;
    }

    .brand {
      gap: 6px;
    }

    .brand-name {
      font-size: 0.87rem;
    }

    .header-separator {
      margin: 0 2px;
    }

    .conversation-header {
      padding: 0 13px;
    }

    .conversation-main {
      padding: 14px 14px 18px;
    }

    .composer {
      padding: 0 8px max(8px, env(safe-area-inset-bottom));
    }

    .composer-box {
      min-height: 136px;
      padding: 10px;
      gap: 4px;
    }

    .composer-box textarea {
      min-height: 76px;
      padding-right: 2px;
      padding-left: 2px;
    }

    .send-button {
      width: 34px;
      height: 34px;
      flex-basis: 34px;
    }

    .msg {
      padding: 15px 0;
    }
  }

  @media (max-width: 480px) {
    .auth-shell {
      padding: 16px;
      background-size: 36px 36px;
    }

    .auth-card {
      padding: 30px 24px;
    }

    .auth-heading {
      margin: 34px 0 24px;
    }

    .header-actions {
      gap: 0;
    }

    .header-button,
    .icon-button {
      width: 31px;
    }

    .brand-mark {
      width: 16px;
      flex-basis: 16px;
    }

    .thread-rail,
    .thread-rail.collapsed,
    .memory-rail,
    .memory-rail.collapsed {
      width: 100%;
    }

    .profile-drawer {
      width: 100%;
      max-width: none;
      border-left: 0;
    }

    .drawer-body {
      padding: 14px;
    }

    .drawer-actions {
      flex-wrap: wrap;
      padding-right: 14px;
      padding-left: 14px;
    }

    .drawer-actions .danger-button {
      margin-left: 0;
    }

    .composer-toolbar {
      gap: 6px;
    }

    .composer-selectors {
      flex: 1;
      overflow: hidden;
    }

    .composer-selector {
      flex: 0 1 auto;
      padding: 0 2px;
    }

    .selector-leading {
      display: none;
    }

    .agent-selector {
      max-width: 56%;
    }

    .model-selector {
      max-width: 44%;
    }

    .agent-select {
      max-width: 120px;
    }

    .model-select {
      max-width: 92px;
    }
  }

  @media (hover: none) {
    .row-actions {
      opacity: 1;
    }

    .row-actions .icon-button {
      width: 34px;
      height: 40px;
    }

    .message-action {
      width: 36px;
      height: 36px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      scroll-behavior: auto !important;
      transition-duration: 0.01ms !important;
    }
  }
`;
