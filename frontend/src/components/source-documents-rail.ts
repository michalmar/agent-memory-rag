import { html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import type { DirectiveSourceItem } from '../client.js';
import type { ResourceStatus } from '../chat-models.js';
import {
  DIRECTIVE_SOURCE_RETENTION_NOTICE,
  type DirectiveSourceUploadStatus,
} from '../source-documents.js';
import { LightDomElement } from './light-dom-element.js';

export interface SourceDocumentsRailActions {
  close: () => void;
  upload: (file: File) => void;
  loadMore: () => void;
  retry: () => void;
  requestDelete: (filename: string) => void;
  cancelDelete: () => void;
  confirmDelete: (filename: string) => void;
}

@customElement('source-documents-rail')
export class SourceDocumentsRail extends LightDomElement {
  @property({ type: Boolean }) open = false;
  @property({ type: Boolean }) canManage = false;
  @property({ attribute: false }) documents: DirectiveSourceItem[] = [];
  @property() status: ResourceStatus = 'loading';
  @property() uploadStatus: DirectiveSourceUploadStatus = 'idle';
  @property({ type: Number }) uploadProgress = 0;
  @property() uploadError = '';
  @property() deletingFilename: string | null = null;
  @property({ type: Boolean }) deleting = false;
  @property() deleteError = '';
  @property() nextCursor: string | null = null;
  @property({ type: Boolean }) loadingMore = false;
  @property({ attribute: false }) actions!: SourceDocumentsRailActions;

  render() {
    return html`
      <aside
        id="source-documents-panel"
        class="memory-rail ${this.open ? '' : 'collapsed'}"
        aria-label="Directive source documents"
      >
        <header class="rail-header">
          <div class="rail-heading">
            <p class="rail-eyebrow">Ingestion input</p>
            <h2 class="rail-title">Source documents</h2>
          </div>
          <span class="rail-count">
            ${this.status === 'ready' ? this.documents.length : '—'}
          </span>
          <button
            class="icon-button"
            type="button"
            aria-label="Close source documents"
            @click=${this.actions.close}
          >
            <span class="material-symbols-outlined">right_panel_close</span>
          </button>
        </header>

        <div class="rail-content">
          ${this.renderUpload()}
          <div class="rail-section-label">
            <span>Current source PDFs</span>
            <span>${this.documents.length}</span>
          </div>
          <div class="source-list">
            ${this.renderDocuments()}
          </div>
          ${this.nextCursor
            ? html`<button
                class="secondary-button source-load-more"
                type="button"
                ?disabled=${this.loadingMore}
                @click=${this.actions.loadMore}
              >
                ${this.loadingMore ? 'Loading…' : 'Load more'}
              </button>`
            : nothing}
        </div>
      </aside>
    `;
  }

  private renderUpload() {
    if (!this.canManage) {
      return html`<div class="source-notice" role="status">
        Source management requires an assigned application role.
      </div>`;
    }
    const uploading = this.uploadStatus === 'uploading';
    return html`
      <section class="source-upload" aria-labelledby="source-upload-title">
        <div>
          <p class="rail-eyebrow" id="source-upload-title">Add source</p>
          <p class="source-upload-note">
            Upload a PDF named
            <code>12345678-policy-name-v1.pdf</code>. Existing files are never
            overwritten.
          </p>
        </div>
        <input
          id="directive-source-file"
          class="sr-only"
          type="file"
          accept=".pdf,application/pdf"
          ?disabled=${uploading}
          @change=${this.onFileSelected}
        />
        <button
          class="primary-button source-upload-button"
          type="button"
          ?disabled=${uploading}
          @click=${this.openFilePicker}
        >
          <span class="material-symbols-outlined">upload_file</span>
          ${uploading ? 'Uploading…' : 'Choose PDF'}
        </button>
        ${uploading
          ? html`<div
              class="source-progress"
              role="progressbar"
              aria-label="Upload progress"
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow=${this.uploadProgress}
            >
              <span style=${`width: ${this.uploadProgress}%`}></span>
            </div>`
          : nothing}
        ${this.uploadError
          ? html`<p class="source-error" role="alert">${this.uploadError}</p>`
          : nothing}
      </section>
    `;
  }

  private renderDocuments() {
    if (this.status === 'loading') {
      return html`<div class="empty-list" role="status">
        Loading source documents…
      </div>`;
    }
    if (this.status === 'error') {
      return html`<div class="empty-list" role="alert">
        <span>Source documents are temporarily unavailable.</span>
        <button
          class="secondary-button"
          type="button"
          @click=${this.actions.retry}
        >
          Try again
        </button>
      </div>`;
    }
    if (this.documents.length === 0) {
      return html`<div class="empty-list">
        No source PDFs have been uploaded.
      </div>`;
    }
    return this.documents.map((document) => html`
      <article class="source-entry">
        <div class="source-row">
          <div class="source-main">
            <span class="row-title">${document.filename}</span>
            <span class="row-meta">
              ${formatBytes(document.size_bytes)}
              · ${formatDate(document.last_modified)}
            </span>
          </div>
          <div class="row-actions">
            <button
              class="icon-button"
              type="button"
              aria-label=${`Delete ${document.filename}`}
              ?disabled=${this.deleting}
              @click=${() => this.actions.requestDelete(document.filename)}
            >
              <span class="material-symbols-outlined">delete</span>
            </button>
          </div>
        </div>
        ${this.deletingFilename === document.filename
          ? html`<div
              class="source-delete-confirmation"
              role="alertdialog"
              aria-label=${`Confirm deletion of ${document.filename}`}
            >
              <p>
                Delete <strong>${document.filename}</strong> from the ingestion
                source?
              </p>
              <p>${DIRECTIVE_SOURCE_RETENTION_NOTICE}</p>
              ${this.deleteError
                ? html`<p class="source-error" role="alert">
                    ${this.deleteError}
                  </p>`
                : nothing}
              <div class="source-delete-actions">
                <button
                  class="danger-button"
                  type="button"
                  ?disabled=${this.deleting}
                  @click=${() =>
                    this.actions.confirmDelete(document.filename)}
                >
                  ${this.deleting ? 'Deleting…' : 'Delete source'}
                </button>
                <button
                  class="secondary-button"
                  type="button"
                  ?disabled=${this.deleting}
                  @click=${this.actions.cancelDelete}
                >
                  Cancel
                </button>
              </div>
            </div>`
          : nothing}
      </article>
    `);
  }

  private openFilePicker = (): void => {
    this.querySelector<HTMLInputElement>('#directive-source-file')?.click();
  };

  private onFileSelected = (event: Event): void => {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (file) this.actions.upload(file);
  };
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? 'Unknown date'
    : date.toLocaleString();
}
