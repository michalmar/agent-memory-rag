import { html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import { getConfig } from '../auth.js';
import type { ConversationSummary } from '../client.js';
import type { ResourceStatus } from '../chat-models.js';
import { LightDomElement } from './light-dom-element.js';

const MOCK_USERS = ['user-alice', 'user-bob', 'user-charlie'];

export interface ConversationSidebarActions {
  close: () => void;
  newConversation: () => void;
  toggleMemory: () => void;
  openProfile: (trigger?: HTMLElement) => void;
  switchMockUser: (userId: string) => void;
  signOut: () => void;
  openConversation: (conversationId: string) => void;
  saveMemory: (conversation: ConversationSummary) => void;
  renameConversation: (conversation: ConversationSummary) => void;
  deleteConversation: (conversation: ConversationSummary) => void;
}

@customElement('conversation-sidebar')
export class ConversationSidebar extends LightDomElement {
  @property({ type: Boolean }) open = false;
  @property({ attribute: false }) conversations: ConversationSummary[] = [];
  @property() status: ResourceStatus = 'loading';
  @property({ attribute: false }) memorisedIds = new Set<string>();
  @property() activeConversationId: string | null = null;
  @property() initials = '';
  @property() userName = '';
  @property() userEmail: string | null = null;
  @property() mockUser = '';
  @property({ attribute: false }) actions!: ConversationSidebarActions;

  render() {
    return html`
      <aside
        id="thread-panel"
        class="thread-rail ${this.open ? '' : 'collapsed'}"
        aria-label="Thread history"
      >
        <header class="rail-header">
          <div class="rail-heading">
            <h2 class="rail-title">Threads</h2>
          </div>
          <span class="rail-count">
            ${this.status === 'ready' ? this.conversations.length : '—'}
          </span>
          <button
            class="icon-button"
            type="button"
            aria-label="Close thread history"
            @click=${this.actions.close}
          >
            <span class="material-symbols-outlined">left_panel_close</span>
          </button>
        </header>

        <div class="rail-content">
          <div class="rail-actions" aria-label="Thread actions">
            <button
              class="rail-action"
              type="button"
              aria-label="Start a new thread"
              title="Start a new thread"
              @click=${this.actions.newConversation}
            >
              <span class="material-symbols-outlined">edit_square</span>
            </button>
            <button
              class="rail-action"
              type="button"
              aria-label="Open saved memory"
              title="Saved memory"
              @click=${this.actions.toggleMemory}
            >
              <span class="material-symbols-outlined">database</span>
            </button>
            <button
              class="rail-action"
              type="button"
              aria-label="Open memory profile"
              title="Memory profile"
              @click=${(event: Event) =>
                this.actions.openProfile(event.currentTarget as HTMLElement)}
            >
              <span class="material-symbols-outlined">person</span>
            </button>
          </div>

          <div class="rail-section-label">
            <span>Recent</span>
            <span>${this.conversations.length}</span>
          </div>

          <div class="thread-list">
            ${this.renderConversations()}
          </div>
        </div>

        <div class="identity-card">
          <span class="identity-label">
            ${getConfig().authMode === 'mock' ? 'Demo identity' : 'Signed in'}
          </span>
          <div class="identity-row">
            <div class="avatar" aria-hidden="true" title=${this.userName}>
              ${this.initials}
            </div>
            <div class="identity-control">
              <div class="identity-name">${this.userName}</div>
              ${getConfig().authMode === 'mock'
                ? html`<select
                    class="identity-select"
                    aria-label="Switch demo identity"
                    .value=${this.mockUser}
                    @change=${(event: Event) =>
                      this.actions.switchMockUser(
                        (event.target as HTMLSelectElement).value,
                      )}
                  >
                    ${MOCK_USERS.map(
                      (user) => html`<option value=${user}>${user}</option>`,
                    )}
                  </select>`
                : html`<div class="row-meta">
                    ${this.userEmail ?? 'Authenticated account'}
                  </div>`}
            </div>
            <button
              class="icon-button profile-button"
              type="button"
              aria-label="Edit memory profile"
              title="Edit memory profile"
              @click=${(event: Event) =>
                this.actions.openProfile(event.currentTarget as HTMLElement)}
            >
              <span class="material-symbols-outlined">manage_accounts</span>
            </button>
            ${getConfig().authMode === 'mock'
              ? nothing
              : html`<button
                  class="icon-button sign-out-button"
                  type="button"
                  aria-label="Sign out"
                  title="Sign out"
                  @click=${this.actions.signOut}
                >
                  <span class="material-symbols-outlined">logout</span>
                </button>`}
          </div>
        </div>
      </aside>
    `;
  }

  private renderConversations() {
    if (this.status === 'loading') {
      return html`<div class="empty-list" role="status">Loading threads…</div>`;
    }
    if (this.status === 'error') {
      return html`<div class="empty-list" role="status">
        Threads are temporarily unavailable.
      </div>`;
    }
    if (this.conversations.length === 0) {
      return html`<div class="empty-list">No threads for this account yet.</div>`;
    }
    return this.conversations.map((conversation) =>
      this.renderConversation(conversation));
  }

  private renderConversation(conversation: ConversationSummary) {
    const memorised = this.memorisedIds.has(conversation.id);
    const active = this.activeConversationId === conversation.id;
    const title = conversation.title ?? 'Untitled thread';
    return html`
      <div class="thread-row ${active ? 'active' : ''}">
        <button
          class="thread-main"
          type="button"
          aria-current=${active ? 'true' : nothing}
          @click=${() => this.actions.openConversation(conversation.id)}
        >
          <span class="row-title">${title}</span>
          <span class="row-meta">
            ${conversation.message_count ?? 0}
            ${(conversation.message_count ?? 0) === 1 ? 'message' : 'messages'}
            ${conversation.metadata?.agent_label
              ? ` / ${conversation.metadata.agent_label}`
              : ''}
          </span>
        </button>
        <div class="row-actions">
          <button
            class="icon-button"
            type="button"
            title=${memorised ? 'Saved to memory' : 'Save to memory'}
            aria-label=${memorised ? 'Saved to memory' : `Save ${title} to memory`}
            ?disabled=${memorised}
            @click=${(event: Event) => {
              event.stopPropagation();
              this.actions.saveMemory(conversation);
            }}
          >
            <span class="material-symbols-outlined">
              ${memorised ? 'bookmark_added' : 'bookmark_add'}
            </span>
          </button>
          <button
            class="icon-button"
            type="button"
            aria-label=${`Rename ${title}`}
            @click=${(event: Event) => {
              event.stopPropagation();
              this.actions.renameConversation(conversation);
            }}
          >
            <span class="material-symbols-outlined">edit</span>
          </button>
          <button
            class="icon-button"
            type="button"
            aria-label=${`Delete ${title}`}
            @click=${(event: Event) => {
              event.stopPropagation();
              this.actions.deleteConversation(conversation);
            }}
          >
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
      </div>
    `;
  }
}
