import { html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import type { MemoryRow } from '../client.js';
import type { ResourceStatus } from '../chat-models.js';
import { LightDomElement } from './light-dom-element.js';

export interface MemoryRailActions {
  close: () => void;
  updateQuery: (query: string) => void;
  search: () => void;
  clearSearch: () => void;
  select: (memory: MemoryRow) => void;
  clearSelection: () => void;
  openConversation: (conversationId: string) => void;
  deleteMemory: (memory: MemoryRow) => void;
}

@customElement('memory-rail')
export class MemoryRail extends LightDomElement {
  @property({ type: Boolean }) open = false;
  @property({ attribute: false }) memories: MemoryRow[] = [];
  @property({ attribute: false }) searchResults: MemoryRow[] | null = null;
  @property({ attribute: false }) selectedMemory: MemoryRow | null = null;
  @property() status: ResourceStatus = 'loading';
  @property() query = '';
  @property({ attribute: false }) actions!: MemoryRailActions;

  render() {
    const visibleMemories = this.searchResults ?? this.memories;
    return html`
      <aside
        id="memory-panel"
        class="memory-rail ${this.open ? '' : 'collapsed'}"
        aria-label="Saved memory"
      >
        <header class="rail-header">
          <div class="rail-heading">
            <p class="rail-eyebrow">Semantic layer</p>
            <h2 class="rail-title">Saved memory</h2>
          </div>
          <span class="rail-count">
            ${this.status === 'ready' ? this.memories.length : '—'}
          </span>
          <button
            class="icon-button"
            type="button"
            aria-label="Close saved memory"
            @click=${this.actions.close}
          >
            <span class="material-symbols-outlined">right_panel_close</span>
          </button>
        </header>

        <div class="rail-content">
          ${this.selectedMemory
            ? this.renderDetail(this.selectedMemory)
            : html`
                <div class="memory-search" role="search">
                  <label class="memory-search-field">
                    <span class="sr-only">Search saved memory</span>
                    <span class="material-symbols-outlined">search</span>
                    <input
                      type="search"
                      placeholder="Search by meaning"
                      .value=${this.query}
                      @input=${(event: Event) =>
                        this.actions.updateQuery(
                          (event.target as HTMLInputElement).value,
                        )}
                      @keydown=${(event: KeyboardEvent) => {
                        if (event.key === 'Enter') this.actions.search();
                      }}
                    />
                  </label>
                  ${this.searchResults
                    ? html`<button
                        class="icon-button"
                        type="button"
                        aria-label="Clear memory search"
                        @click=${this.actions.clearSearch}
                      >
                        <span class="material-symbols-outlined">close</span>
                      </button>`
                    : html`<button
                        class="icon-button"
                        type="button"
                        aria-label="Search saved memory"
                        @click=${this.actions.search}
                      >
                        <span class="material-symbols-outlined">arrow_forward</span>
                      </button>`}
                </div>

                <div class="rail-section-label">
                  <span>${this.searchResults ? 'Matches' : 'Saved context'}</span>
                  <span>${visibleMemories.length}</span>
                </div>

                <div class="memory-list">
                  ${this.renderMemories(visibleMemories)}
                </div>
              `}
        </div>
      </aside>
    `;
  }

  private renderMemories(memories: MemoryRow[]) {
    if (this.status === 'loading' && !this.searchResults) {
      return html`<div class="empty-list" role="status">
        Loading saved memory…
      </div>`;
    }
    if (this.status === 'error' && !this.searchResults) {
      return html`<div class="empty-list" role="status">
        Saved memory is temporarily unavailable.
      </div>`;
    }
    if (memories.length === 0) {
      return html`<div class="empty-list">
        ${this.searchResults
          ? 'No matching memory. Try a broader phrase.'
          : 'Save a completed thread to make its context searchable here.'}
      </div>`;
    }
    return memories.map((memory) => this.renderMemory(memory));
  }

  private renderMemory(memory: MemoryRow) {
    const summary = memory.summary?.trim() || 'No summary available';
    const excerpt = summary.length > 64 ? `${summary.slice(0, 64)}…` : summary;
    const active = this.selectedMemory?.id === memory.id;
    const title = memory.source_title ?? 'Saved memory';
    return html`
      <div class="memory-row ${active ? 'active' : ''}">
        <button
          class="memory-main"
          type="button"
          aria-current=${active ? 'true' : nothing}
          @click=${() => this.actions.select(memory)}
        >
          <span class="row-title">${title}</span>
          <span class="row-meta">${excerpt}</span>
        </button>
        ${typeof memory.similarity === 'number'
          ? html`<span class="similarity">
              ${Math.round(memory.similarity * 100)}%
            </span>`
          : nothing}
        <div class="row-actions">
          <button
            class="icon-button"
            type="button"
            aria-label=${`Delete ${title}`}
            @click=${(event: Event) => {
              event.stopPropagation();
              this.actions.deleteMemory(memory);
            }}
          >
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
      </div>
    `;
  }

  private renderDetail(memory: MemoryRow) {
    return html`
      <article class="memory-detail">
        <button
          class="icon-button memory-detail-back"
          type="button"
          aria-label="Back to saved memory"
          @click=${this.actions.clearSelection}
        >
          <span class="material-symbols-outlined">arrow_back</span>
        </button>
        <p class="rail-eyebrow">Saved context</p>
        <h2>${memory.source_title ?? 'Saved memory'}</h2>
        <div class="memory-detail-meta">
          <span>Thread ${memory.conversation_id.slice(0, 8)}</span>
          ${typeof memory.similarity === 'number'
            ? html`<span>${Math.round(memory.similarity * 100)}% match</span>`
            : nothing}
          ${memory.created_at
            ? html`<span>${new Date(memory.created_at).toLocaleString()}</span>`
            : nothing}
        </div>
        <p class="memory-summary">${memory.summary}</p>
        <div class="memory-detail-actions">
          <button
            class="primary-button"
            type="button"
            @click=${() =>
              this.actions.openConversation(memory.conversation_id)}
          >
            Open thread
          </button>
          <button
            class="secondary-button"
            type="button"
            @click=${this.actions.clearSelection}
          >
            Back
          </button>
        </div>
      </article>
    `;
  }
}
