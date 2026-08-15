import { html, nothing, type PropertyValues } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';

import type { DirectiveDocument } from '../client.js';
import {
  locateDirectiveHeading,
  directivePdfDownloadFilename,
  type DirectiveCitationTarget,
  type DirectiveDocumentLoadStatus,
  type DirectiveDocumentReference,
  type DirectiveDocumentTab,
} from '../directive-documents.js';
import { renderSafeDirectiveMarkdown } from '../markdown.js';
import {
  focusableElements,
  trapFocusWithin,
} from './focus-trap.js';
import { LightDomElement } from './light-dom-element.js';

export interface DirectiveDocumentViewerActions {
  close: () => void;
  selectTab: (tab: DirectiveDocumentTab) => void;
  retryDocument: () => void;
  retryPdf: () => void;
}

@customElement('directive-document-viewer')
export class DirectiveDocumentViewer extends LightDomElement {
  @property({ type: Boolean }) open = false;
  @property({ attribute: false }) reference: DirectiveDocumentReference | null =
    null;
  @property({ attribute: false }) target: DirectiveCitationTarget | null = null;
  @property({ type: Number }) targetRevision = 0;
  @property({ attribute: false }) document: DirectiveDocument | null = null;
  @property() documentStatus: DirectiveDocumentLoadStatus = 'idle';
  @property() documentError = '';
  @property() activeTab: DirectiveDocumentTab = 'document';
  @property() pdfStatus: DirectiveDocumentLoadStatus = 'idle';
  @property() pdfError = '';
  @property() pdfUrl: string | null = null;
  @property() pdfFilename: string | null = null;
  @property({ attribute: false }) actions!: DirectiveDocumentViewerActions;
  @state() private citationLocationStatus:
    'idle' | 'locating' | 'located' | 'unavailable' = 'idle';

  private keydownRoot?: Document | ShadowRoot;
  private pdfKeyboardWindow?: Window;
  private citationNavigationGeneration = 0;

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
    if (!this.open || !this.target) {
      ++this.citationNavigationGeneration;
      this.clearCitationHeading();
      if (
        this.open
        && this.activeTab === 'document'
        && changed.has('targetRevision')
      ) {
        this.querySelector<HTMLElement>('.document-viewer-body')?.scrollTo({
          top: 0,
          behavior: 'auto',
        });
      }
      if (this.citationLocationStatus !== 'idle') {
        this.citationLocationStatus = 'idle';
      }
    } else if (
      this.activeTab === 'document'
      && this.documentStatus === 'ready'
      && this.document
      && (
        changed.has('open')
        || changed.has('document')
        || changed.has('documentStatus')
        || changed.has('activeTab')
        || changed.has('target')
        || changed.has('targetRevision')
      )
    ) {
      void this.navigateToCitationTarget();
    }
  }

  private async navigateToCitationTarget(): Promise<void> {
    const target = this.target;
    const revision = this.targetRevision;
    const generation = ++this.citationNavigationGeneration;
    if (!target || this.activeTab !== 'document') return;

    if (this.citationLocationStatus !== 'locating') {
      this.citationLocationStatus = 'locating';
      await this.updateComplete;
    }
    if (
      generation !== this.citationNavigationGeneration
      || revision !== this.targetRevision
    ) return;

    let article = this.querySelector<HTMLElement>('.document-markdown');
    if (!article) return;
    let headings = Array.from(
      article.querySelectorAll<HTMLElement>('h2, h3, h4, h5, h6'),
    );
    const location = locateDirectiveHeading(
      headings.map((heading) => heading.textContent ?? ''),
      target,
    );
    const status = location.kind === 'unavailable'
      ? 'unavailable'
      : 'located';
    this.citationLocationStatus = status;
    await this.updateComplete;
    if (
      generation !== this.citationNavigationGeneration
      || revision !== this.targetRevision
    ) return;

    article = this.querySelector<HTMLElement>('.document-markdown');
    if (!article) return;
    headings = Array.from(
      article.querySelectorAll<HTMLElement>('h2, h3, h4, h5, h6'),
    );
    this.clearCitationHeading();
    if (location.kind === 'unavailable') {
      this.querySelector<HTMLElement>('.document-viewer-body')?.scrollTo({
        top: 0,
        behavior: 'auto',
      });
      return;
    }

    const targetElement = location.kind === 'document-top'
      ? article.querySelector<HTMLElement>('h1') ?? article
      : headings[location.index];
    if (!targetElement) return;
    targetElement.id = 'directive-cited-location';
    targetElement.tabIndex = -1;
    targetElement.dataset.directiveCitationTarget = 'true';
    targetElement.classList.remove('document-citation-target');
    void targetElement.offsetWidth;
    targetElement.classList.add('document-citation-target');
    targetElement.scrollIntoView({ block: 'start', behavior: 'auto' });
    targetElement.focus({ preventScroll: true });
  }

  private clearCitationHeading(): void {
    const heading = this.querySelector<HTMLElement>(
      '[data-directive-citation-target]',
    );
    if (!heading) return;
    heading.classList.remove('document-citation-target');
    heading.removeAttribute('data-directive-citation-target');
    heading.removeAttribute('tabindex');
    if (heading.id === 'directive-cited-location') heading.removeAttribute('id');
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
        ${this.renderCitationLocation()}

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

  private renderCitationLocation() {
    const target = this.target;
    if (!target) return nothing;
    const sourceLabel = target.sourceIndex == null
      ? ''
      : `Source ${target.sourceIndex + 1}`;
    const sectionLabel = target.sectionNumber || target.sectionTitle
      ? [
          target.sectionNumber
            ? `Section ${target.sectionNumber}`
            : 'Section',
          target.sectionTitle,
        ].filter(Boolean).join(' · ')
      : '';
    const pageLabel = target.pageFrom == null
      ? ''
      : target.pageTo != null && target.pageTo !== target.pageFrom
        ? `Pages ${target.pageFrom}–${target.pageTo}`
        : `Page ${target.pageFrom}`;
    const details = [sourceLabel, sectionLabel, pageLabel].filter(Boolean);
    const liveMessage = {
      idle: '',
      locating: 'Locating the cited section.',
      located: 'Cited section located.',
      unavailable:
        'The cited Markdown section could not be located. The document is open at the top.',
    }[this.citationLocationStatus];
    return html`
      <div
        class="document-citation-location"
        data-status=${this.citationLocationStatus}
      >
        <span
          class="document-citation-icon material-symbols-outlined"
          aria-hidden="true"
        >location_on</span>
        <div class="document-citation-copy">
          <strong>Cited location</strong>
          <span>${details.join(' · ') || 'Location metadata unavailable'}</span>
          ${this.citationLocationStatus === 'unavailable'
            ? html`<span class="document-citation-warning">
                Section unavailable in Markdown; use the cited PDF page.
              </span>`
            : nothing}
        </div>
        <span
          class="document-citation-live"
          role="status"
          aria-live="polite"
        >${liveMessage}</span>
      </div>
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
      <article class="document-markdown">
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
    const downloadFilename = directivePdfDownloadFilename(
      this.document?.source_filename,
      this.pdfFilename,
    );
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
          ${downloadFilename
            ? html`<a
                class="secondary-button"
                href=${this.pdfUrl}
                download=${downloadFilename}
              >
                <span class="material-symbols-outlined" aria-hidden="true">
                  download
                </span>
                Download
              </a>`
            : nothing}
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
