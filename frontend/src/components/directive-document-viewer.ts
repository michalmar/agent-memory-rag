import { html, nothing, type PropertyValues } from 'lit';
import { customElement, property } from 'lit/decorators.js';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';

import type { DirectiveDocument } from '../client.js';
import type {
  DirectiveDocumentLoadStatus,
  DirectiveDocumentReference,
  DirectiveDocumentTab,
} from '../directive-documents.js';
import { directiveReferenceFromPdfHref } from '../directive-documents.js';
import { renderSafeDirectiveMarkdown } from '../markdown.js';
import {
  focusableElements,
  trapFocusWithin,
} from './focus-trap.js';
import { LightDomElement } from './light-dom-element.js';

export interface DirectiveDocumentViewerActions {
  close: () => void;
  selectTab: (tab: DirectiveDocumentTab) => void;
  openLinkedDocument: (
    reference: DirectiveDocumentReference,
    trigger?: HTMLElement,
  ) => void;
  retryDocument: () => void;
  retryPdf: () => void;
}

@customElement('directive-document-viewer')
export class DirectiveDocumentViewer extends LightDomElement {
  @property({ type: Boolean }) open = false;
  @property({ attribute: false }) reference: DirectiveDocumentReference | null =
    null;
  @property({ attribute: false }) document: DirectiveDocument | null = null;
  @property() documentStatus: DirectiveDocumentLoadStatus = 'idle';
  @property() documentError = '';
  @property() activeTab: DirectiveDocumentTab = 'document';
  @property() pdfStatus: DirectiveDocumentLoadStatus = 'idle';
  @property() pdfError = '';
  @property() pdfUrl: string | null = null;
  @property({ attribute: false }) actions!: DirectiveDocumentViewerActions;

  private keydownRoot?: Document | ShadowRoot;
  private pdfKeyboardWindow?: Window;

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeFocusTrap();
    this.removePdfKeyboardBridge();
  }

  protected updated(changed: PropertyValues): void {
    if (changed.has('open') && this.open) {
      this.installFocusTrap();
    } else if (changed.has('open')) {
      this.removeFocusTrap();
    }
    if (
      this.open
      && (changed.has('open') || changed.has('reference'))
    ) {
      this.querySelector<HTMLElement>('.document-viewer-close')?.focus();
    }
    if (
      (changed.has('open') && !this.open)
      || (changed.has('activeTab') && this.activeTab !== 'pdf')
      || (changed.has('pdfUrl') && !this.pdfUrl)
    ) {
      this.removePdfKeyboardBridge();
    }
  }

  private installFocusTrap(): void {
    this.removeFocusTrap();
    const root = this.getRootNode();
    if (root instanceof Document || root instanceof ShadowRoot) {
      this.keydownRoot = root;
      root.addEventListener('keydown', this.onRootKeydown);
    }
  }

  private removeFocusTrap(): void {
    this.keydownRoot?.removeEventListener('keydown', this.onRootKeydown);
    this.keydownRoot = undefined;
  }

  private onRootKeydown = (event: Event): void => {
    if (
      !this.open
      || !(event instanceof KeyboardEvent)
      || event.key !== 'Tab'
    ) return;
    const drawer = this.querySelector<HTMLElement>('.document-viewer');
    if (drawer) trapFocusWithin(event, drawer);
  };

  render() {
    if (!this.open || !this.reference) return nothing;
    const title = this.document?.title ?? this.reference.sourceName;
    const version =
      this.document?.version_label ?? this.reference.versionLabel;
    const effectiveFrom =
      this.document?.effective_from ?? this.reference.effectiveFrom;
    return html`
      <button
        class="document-viewer-scrim"
        type="button"
        aria-label="Close directive document"
        @click=${this.actions.close}
      ></button>
      <section
        class="document-viewer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="directive-document-title"
      >
        <span
          class="document-focus-guard"
          tabindex="0"
          data-focus-guard
          @focus=${() => this.focusBoundary('last')}
        ></span>
        <header class="document-viewer-header">
          <div class="document-viewer-heading">
            <p class="rail-eyebrow">Directive document</p>
            <h2 id="directive-document-title">${title}</h2>
            <p class="document-viewer-metadata">
              ${version ? html`<span>Version ${version}</span>` : nothing}
              ${effectiveFrom
                ? html`<span>Effective ${effectiveFrom}</span>`
                : nothing}
              ${this.document
                ? html`<span>${this.document.total_pages} pages</span>`
                : nothing}
            </p>
          </div>
          <button
            class="icon-button document-viewer-close"
            type="button"
            aria-label="Close directive document"
            @click=${this.actions.close}
          >
            <span class="material-symbols-outlined">close</span>
          </button>
        </header>

        <div class="document-viewer-tabs" role="tablist" aria-label="Document format">
          ${this.renderTab('document', 'Document')}
          ${this.renderTab('pdf', 'Original PDF')}
        </div>

        <div
          class="document-viewer-body"
          role="tabpanel"
          aria-labelledby=${`directive-${this.activeTab}-tab`}
          tabindex="0"
          aria-busy=${this.activeTab === 'document'
            ? this.documentStatus === 'loading'
            : this.pdfStatus === 'loading'}
        >
          ${this.activeTab === 'document'
            ? this.renderDocument()
            : this.renderPdf(title)}
        </div>
        <span
          class="document-focus-guard"
          tabindex="0"
          data-focus-guard
          @focus=${() => this.focusBoundary('first')}
        ></span>
      </section>
    `;
  }

  private renderTab(tab: DirectiveDocumentTab, label: string) {
    const selected = this.activeTab === tab;
    return html`
      <button
        id=${`directive-${tab}-tab`}
        class="document-viewer-tab"
        type="button"
        role="tab"
        aria-selected=${selected}
        tabindex=${selected ? '0' : '-1'}
        @click=${() => this.actions.selectTab(tab)}
        @keydown=${(event: KeyboardEvent) =>
          this.onTabKeydown(event, tab)}
      >
        ${label}
      </button>
    `;
  }

  private renderDocument() {
    if (this.documentStatus === 'loading' || this.documentStatus === 'idle') {
      return this.renderStatus('Loading document…');
    }
    if (this.documentStatus === 'error') {
      return this.renderError(
        this.documentError || 'The document could not be loaded.',
        this.actions.retryDocument,
      );
    }
    if (!this.document) {
      return this.renderError(
        'The document response was empty.',
        this.actions.retryDocument,
      );
    }
    return html`
      <article class="document-markdown" @click=${this.onMarkdownClick}>
        ${unsafeHTML(renderSafeDirectiveMarkdown(this.document.markdown))}
      </article>
    `;
  }

  private renderPdf(title: string) {
    if (this.pdfStatus === 'loading' || this.pdfStatus === 'idle') {
      return this.renderStatus('Loading original PDF…');
    }
    if (this.pdfStatus === 'error') {
      return this.renderError(
        this.pdfError || 'The original PDF could not be loaded.',
        this.actions.retryPdf,
      );
    }
    if (!this.pdfUrl) {
      return this.renderError(
        'The PDF response was empty.',
        this.actions.retryPdf,
      );
    }
    return html`
      <div class="document-pdf">
        <div class="document-pdf-actions">
          <a
            class="secondary-button"
            href=${this.pdfUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <span class="material-symbols-outlined" aria-hidden="true">
              open_in_new
            </span>
            Open in new tab
          </a>
          <a
            class="secondary-button"
            href=${this.pdfUrl}
            download=${this.document?.source_filename
              ?? `${this.reference?.directiveId ?? 'directive'}.pdf`}
          >
            <span class="material-symbols-outlined" aria-hidden="true">
              download
            </span>
            Download
          </a>
        </div>
        <iframe
          class="document-pdf-frame"
          src=${this.pdfUrl}
          title=${`Original PDF: ${title}`}
          @load=${this.installPdfKeyboardBridge}
        ></iframe>
        <p class="document-pdf-fallback">
          If your browser cannot display the PDF, open it in a new tab or
          download the original file.
        </p>
      </div>
    `;
  }

  private renderStatus(message: string) {
    return html`
      <div class="document-viewer-state" role="status">
        <span class="document-viewer-spinner" aria-hidden="true"></span>
        <span>${message}</span>
      </div>
    `;
  }

  private renderError(message: string, retry: () => void) {
    return html`
      <div class="document-viewer-state document-viewer-error" role="alert">
        <span class="material-symbols-outlined" aria-hidden="true">error</span>
        <p>${message}</p>
        <button class="secondary-button" type="button" @click=${retry}>
          Try again
        </button>
      </div>
    `;
  }

  private onMarkdownClick = (event: MouseEvent): void => {
    const target = event.target instanceof Element ? event.target : null;
    const button = target?.closest<HTMLButtonElement>(
      'button[data-directive-pdf-href]',
    );
    const href = button?.dataset.directivePdfHref;
    if (!button || !href) return;

    const reference = directiveReferenceFromPdfHref(
      href,
      button.textContent ?? '',
    );
    if (!reference) return;
    this.actions.openLinkedDocument(reference, button);
  };

  private onTabKeydown(
    event: KeyboardEvent,
    current: DirectiveDocumentTab,
  ): void {
    let next: DirectiveDocumentTab | null = null;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      next = current === 'document' ? 'pdf' : 'document';
    } else if (event.key === 'Home') {
      next = 'document';
    } else if (event.key === 'End') {
      next = 'pdf';
    }
    if (!next) return;

    event.preventDefault();
    this.actions.selectTab(next);
    requestAnimationFrame(() => {
      this.querySelector<HTMLElement>(`#directive-${next}-tab`)?.focus();
    });
  }

  private focusBoundary(boundary: 'first' | 'last'): void {
    const viewer = this.querySelector<HTMLElement>('.document-viewer');
    if (!viewer) return;
    const focusable = focusableElements(viewer);
    const target =
      boundary === 'first' ? focusable[0] : focusable.at(-1);
    target?.focus();
  }

  private installPdfKeyboardBridge = (event: Event): void => {
    this.removePdfKeyboardBridge();
    const frame = event.currentTarget as HTMLIFrameElement;
    const frameWindow = frame.contentWindow;
    if (!frameWindow) return;
    try {
      frameWindow.addEventListener('keydown', this.onPdfKeydown);
      this.pdfKeyboardWindow = frameWindow;
    } catch (error) {
      if (
        error instanceof DOMException
        && error.name === 'SecurityError'
      ) return;
      throw error;
    }
  };

  private removePdfKeyboardBridge(): void {
    if (!this.pdfKeyboardWindow) return;
    try {
      this.pdfKeyboardWindow.removeEventListener(
        'keydown',
        this.onPdfKeydown,
      );
    } catch (error) {
      if (
        !(error instanceof DOMException)
        || error.name !== 'SecurityError'
      ) {
        throw error;
      }
    }
    this.pdfKeyboardWindow = undefined;
  }

  private onPdfKeydown = (event: KeyboardEvent): void => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    this.actions.close();
  };
}
