import DOMPurify from 'dompurify';
import { html, nothing, type PropertyValues } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { marked } from 'marked';

import '../a2ui/surface-renderer.js';
import { agentIcon, agentLabel } from '../agent-presentation.js';
import type { ChatTurn } from '../chat-models.js';
import {
  findCitationByIdentity,
  findCitationBySearchIndex,
  replaceCitationMarkers,
} from '../citations.js';
import type {
  AgentOption,
  AgentType,
  CitationSource,
  TokenUsage,
} from '../client.js';
import { LightDomElement } from './light-dom-element.js';

const NUMBER_FORMATTER = new Intl.NumberFormat();

export interface ChatTranscriptActions {
  copy: (turn: ChatTurn) => void;
  setFeedback: (turn: ChatTurn, feedback: 'up' | 'down') => void;
}

@customElement('chat-transcript')
export class ChatTranscript extends LightDomElement {
  @property({ attribute: false }) turns: ChatTurn[] = [];
  @property({ type: Boolean }) busy = false;
  @property() agentType: AgentType = 'agent-framework';
  @property({ attribute: false }) agentOptions: AgentOption[] = [];
  @property() userName = '';
  @property({ attribute: false }) actions!: ChatTranscriptActions;

  @state() private clock = Date.now();
  private clockTimer?: number;
  private turnTimestampSignature = '';

  connectedCallback(): void {
    super.connectedCallback();
    this.scheduleClock();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    if (this.clockTimer) clearTimeout(this.clockTimer);
  }

  protected updated(changed: PropertyValues): void {
    if (changed.has('turns')) {
      const timestampSignature = this.turns
        .map((turn) => turn.createdAt ?? '')
        .join('\u0000');
      if (timestampSignature !== this.turnTimestampSignature) {
        this.turnTimestampSignature = timestampSignature;
        this.scheduleClock();
      }
      if (this.turns.length > 0) {
        const conversation = this.parentElement;
        if (conversation?.classList.contains('conversation-main')) {
          conversation.scrollTop = conversation.scrollHeight;
        }
      }
    }
  }

  private scheduleClock(): void {
    if (!this.isConnected) return;
    if (this.clockTimer) clearTimeout(this.clockTimer);
    const now = Date.now();
    const hasRecentTurn = this.turns.some((turn) => {
      if (!turn.createdAt) return false;
      const timestamp = Date.parse(turn.createdAt);
      return Number.isFinite(timestamp) && now - timestamp < 60_000;
    });
    this.clockTimer = window.setTimeout(() => {
      this.clock = Date.now();
      this.scheduleClock();
    }, hasRecentTurn ? 1000 : 60_000);
  }

  render() {
    if (this.turns.length === 0) {
      return html`
        <section class="empty-state" aria-labelledby="welcome-title">
          <span class="empty-state-icon material-symbols-outlined" aria-hidden="true">
            forum
          </span>
          <h2 id="welcome-title">Start a thread</h2>
          <p>
            ${this.agentType === 'directive-rag'
              ? 'Search, summarize, compare, or ask about company directives.'
              : 'Ask about an order, a policy, or saved context.'}
          </p>
        </section>
      `;
    }
    return this.turns.map((turn) => this.renderTurn(turn));
  }

  private renderTurn(turn: ChatTurn) {
    const responding =
      turn.role === 'assistant' && this.busy && this.turns.at(-1) === turn;
    const progressLabel = responding
      ? this.progressLabel(turn)
      : undefined;
    const terminalProgress = turn.progress
      && ['cancelled', 'failed'].includes(turn.progress.status)
      ? turn.progress
      : undefined;
    const timeLabel = this.formatRelativeTime(turn.createdAt);
    return html`
      <article class="msg ${turn.role}">
        <div class="message-heading">
          <div class="message-marker" aria-hidden="true">
            <span class="material-symbols-outlined">
              ${turn.role === 'user' ? 'person' : agentIcon(this.agentType)}
            </span>
          </div>
          <span class="message-role">
            ${turn.role === 'user'
              ? this.userName
              : agentLabel(this.agentOptions, this.agentType)}
          </span>
          ${timeLabel
            ? html`<time
                class="message-time"
                datetime=${turn.createdAt ?? ''}
                title=${new Date(turn.createdAt ?? '').toLocaleString()}
              >
                ${timeLabel}
              </time>`
            : nothing}
          ${responding
            ? html`<span
                class="response-state"
                role="status"
                aria-label="Agent is responding"
              >
                <span class="response-dot"></span>
                ${progressLabel ?? (turn.text ? 'Writing' : 'Working')}
              </span>`
            : nothing}
        </div>
        <div class="message-content">
          ${turn.text
            ? html`<div class="message-body" @click=${this.onMessageBodyClick}>
                ${turn.role === 'assistant'
                  ? this.renderMarkdown(turn.text, turn)
                  : turn.text}
              </div>`
            : responding || terminalProgress
              ? html`<div class="message-body message-pending">
                  ${progressLabel
                    ?? terminalProgress?.message
                    ?? 'Preparing a response…'}
                </div>`
              : nothing}
          ${turn.surfaces.length
            ? html`<div class="surfaces">
                ${turn.surfaces.map(
                  (surface) =>
                    html`<a2ui-surface .surface=${surface}></a2ui-surface>`,
                )}
              </div>`
            : nothing}
          ${this.renderToolActivity(turn)}
          ${this.renderSources(turn)}
          ${this.renderAssistantFooter(turn, responding)}
          ${turn.error
            ? html`<div class="error-message" role="alert">
                <span class="material-symbols-outlined">error</span>
                <span>${turn.error}</span>
              </div>`
            : nothing}
        </div>
      </article>
    `;
  }

  private renderToolActivity(turn: ChatTurn) {
    if (turn.role !== 'assistant' || turn.tools.length === 0) return nothing;
    return html`
      <div class="message-tools" aria-label="Tools used">
        <span class="material-symbols-outlined" aria-hidden="true">construction</span>
        <span class="message-tools-label">Tools</span>
        <span class="message-tool-names">
          ${turn.tools.map((tool) => html`<span>${this.toolLabel(tool)}</span>`)}
        </span>
      </div>
    `;
  }

  private renderSources(turn: ChatTurn) {
    if (turn.role !== 'assistant' || turn.citations.length === 0) return nothing;
    return html`
      <div class="message-sources" aria-label="Sources">
        <span class="message-sources-label">Sources</span>
        <div class="message-source-links">
          ${turn.citations.map((citation, index) => {
            const target = this.citationTarget(turn, index);
            const name = this.citationName(citation, index);
            const details = this.citationDetails(citation);
            const url = this.citationUrl(citation);
            const content = html`
              <span class="material-symbols-outlined" aria-hidden="true">
                description
              </span>
              <span class="message-source-text">
                <span class="message-source-name">${name}</span>
                ${details
                  ? html`<span class="message-source-details">${details}</span>`
                  : nothing}
              </span>
              ${this.renderMandatoryStatus(citation)}
            `;
            return url
              ? html`<a
                  id=${target}
                  class="message-source"
                  href=${url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label=${`Source ${index + 1}: ${name} (opens in a new tab)`}
                >
                  ${content}
                </a>`
              : html`<span
                  id=${target}
                  class="message-source"
                  tabindex="-1"
                  title=${citation.ref_id}
                >
                  ${content}
                </span>`;
          })}
        </div>
      </div>
    `;
  }

  private renderAssistantFooter(turn: ChatTurn, responding: boolean) {
    if (turn.role !== 'assistant' || responding || !turn.text) return nothing;
    return html`
      <footer class="message-footer">
        ${turn.usage
          ? html`<span
              class="message-token-count"
              title=${this.tokenDetails(turn.usage)}
            >
              ${this.tokenSummary(turn.usage)}
            </span>`
          : nothing}
        <div class="message-actions" role="group" aria-label="Message actions">
          ${this.renderFeedbackButton(turn, 'up')}
          ${this.renderFeedbackButton(turn, 'down')}
          <button
            class="message-action"
            type="button"
            title="Copy message"
            aria-label="Copy response"
            @click=${() => this.actions.copy(turn)}
          >
            <span class="material-symbols-outlined">content_copy</span>
          </button>
        </div>
      </footer>
    `;
  }

  private renderFeedbackButton(turn: ChatTurn, feedback: 'up' | 'down') {
    const active = turn.feedback === feedback;
    const helpful = feedback === 'up';
    return html`
      <button
        class="message-action ${active ? 'active' : ''}"
        type="button"
        title=${helpful ? 'Helpful' : 'Not helpful'}
        aria-label=${helpful
          ? 'Mark response as helpful'
          : 'Mark response as not helpful'}
        aria-pressed=${active}
        @click=${() => this.actions.setFeedback(turn, feedback)}
      >
        <span class="material-symbols-outlined">
          ${helpful ? 'thumb_up' : 'thumb_down'}
        </span>
      </button>
    `;
  }

  private citationTarget(turn: ChatTurn, index: number): string {
    return `citation-${turn.id}-${index}`;
  }

  private citationName(citation: CitationSource, index: number): string {
    const name = citation.source_name.replace(/【[^】]+】/g, '').trim();
    if (!name || /^mcp:\/\//i.test(name)) {
      return `Foundry IQ source ${index + 1}`;
    }
    if (!/^https?:\/\//i.test(name)) return name;
    try {
      const url = new URL(name);
      return url.pathname.split('/').filter(Boolean).at(-1) || url.hostname;
    } catch {
      return `Source ${index + 1}`;
    }
  }

  private citationDetails(citation: CitationSource): string {
    if (!citation.directive_id) return '';
    const details: string[] = [];
    if (citation.version_label) details.push(`Version ${citation.version_label}`);
    if (citation.section_number || citation.section_title) {
      details.push(
        [
          citation.section_number
            ? `Section ${citation.section_number}`
            : 'Section',
          citation.section_title,
        ].filter(Boolean).join(' · '),
      );
    }
    if (citation.page_from != null) {
      details.push(
        citation.page_to != null && citation.page_to !== citation.page_from
          ? `Pages ${citation.page_from}–${citation.page_to}`
          : `Page ${citation.page_from}`,
      );
    }
    if (citation.effective_from) {
      details.push(`Effective ${citation.effective_from}`);
    }
    return details.join(' · ');
  }

  private renderMandatoryStatus(citation: CitationSource) {
    if (!citation.directive_id) return nothing;
    const status = citation.mandatory_status ?? 'unknown';
    const presentation = {
      mandatory: {
        label: 'Mandatory',
        title: 'This directive is mandatory for the signed-in user.',
      },
      non_mandatory: {
        label: 'Not mandatory',
        title: 'This directive is not mandatory for the signed-in user; it remains available and relevant.',
      },
      unknown: {
        label: 'Status unknown',
        title: 'Mandatory status could not be verified.',
      },
    }[status];
    return html`<span
      class="mandate-badge mandate-${status}"
      title=${presentation.title}
    >${presentation.label}</span>`;
  }

  private citationUrl(citation: CitationSource): string | null {
    if (!citation.url) return null;
    try {
      const url = new URL(citation.url);
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
    } catch {
      return null;
    }
  }

  private escapeAttribute(value: string): string {
    return value.replace(
      /[&<>"']/g,
      (character) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[character] ?? character,
    );
  }

  private linkCitationMarkers(text: string, turn: ChatTurn): string {
    let fallbackIndex = 0;
    return replaceCitationMarkers(text, (marker) => {
      let index = findCitationByIdentity(turn.citations, marker);
      if (index < 0) {
        index = findCitationBySearchIndex(turn.citations, marker);
      }
      if (index < 0 && fallbackIndex < turn.citations.length) {
        index = fallbackIndex;
        fallbackIndex += 1;
      }
      if (index < 0) return `[${marker.sourceName}]`;
      fallbackIndex = Math.max(fallbackIndex, index + 1);
      const target = this.citationTarget(turn, index);
      const label = this.escapeAttribute(
        this.citationName(turn.citations[index], index),
      );
      return `<sup class="inline-citation"><a href="#${target}" data-citation-target="${target}" aria-label="Source ${index + 1}: ${label}">${index + 1}</a></sup>`;
    });
  }

  private renderMarkdown(text: string, turn: ChatTurn) {
    const linked = this.linkCitationMarkers(text, turn);
    const raw = marked.parse(linked, { async: false }) as string;
    return unsafeHTML(DOMPurify.sanitize(raw));
  }

  private onMessageBodyClick = (event: MouseEvent): void => {
    const element = event.target instanceof Element ? event.target : null;
    const link = element?.closest<HTMLAnchorElement>('a[data-citation-target]');
    const target = link?.dataset.citationTarget;
    if (!link || !target) return;
    const source = this.querySelector<HTMLElement>(`#${target}`);
    if (!source) return;
    event.preventDefault();
    source.scrollIntoView({ block: 'nearest' });
    source.focus({ preventScroll: true });
  };

  private formatRelativeTime(createdAt?: string): string {
    if (!createdAt) return '';
    const timestamp = Date.parse(createdAt);
    if (!Number.isFinite(timestamp)) return '';
    const elapsedSeconds = Math.max(
      0,
      Math.floor((this.clock - timestamp) / 1000),
    );
    if (elapsedSeconds < 5) return 'right now';
    if (elapsedSeconds < 60) return `${elapsedSeconds} sec ago`;
    const elapsedMinutes = Math.floor(elapsedSeconds / 60);
    if (elapsedMinutes < 60) return `${elapsedMinutes} min ago`;
    const elapsedHours = Math.floor(elapsedMinutes / 60);
    if (elapsedHours < 24) return `${elapsedHours} hr ago`;
    const elapsedDays = Math.floor(elapsedHours / 24);
    if (elapsedDays < 7) return `${elapsedDays} d ago`;
    return new Date(timestamp).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
    });
  }

  private toolLabel(toolName: string): string {
    const labels: Record<string, string> = {
      knowledge_base_retrieve: 'Foundry IQ',
      get_user_context: 'User context',
      get_order_status: 'Order status',
      check_memory: 'Memory search',
      update_user_profile: 'Profile update',
      resolve_directive: 'Directive resolution',
      search_directives: 'Directive search',
      get_directive_manifest: 'Directive outline',
      get_directive_content: 'Directive content',
      search_within_directive: 'Focused directive search',
      get_related_directives: 'Linked directives',
      get_precomputed_summary: 'Directive summary',
      get_user_directive_mandates: 'Mandatory status',
    };
    return labels[toolName] ?? toolName.replaceAll('_', ' ');
  }

  private progressLabel(turn: ChatTurn): string | undefined {
    const progress = turn.progress;
    if (!progress) return undefined;
    if (
      progress.completed_count != null
      && progress.total_count != null
    ) {
      return `${progress.message} ${progress.completed_count}/${progress.total_count}`;
    }
    return progress.message;
  }

  private tokenSummary(usage: TokenUsage): string {
    return NUMBER_FORMATTER.format(
      (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0),
    ) + ' tokens';
  }

  private tokenDetails(usage: TokenUsage): string {
    const input = usage.input_tokens ?? 0;
    const output = usage.output_tokens ?? 0;
    const cached = usage.cached_tokens ?? 0;
    return `${input.toLocaleString()} input, ${output.toLocaleString()} output${
      cached ? `, ${cached.toLocaleString()} cached` : ''
    }`;
  }
}
