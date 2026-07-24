import { html, type PropertyValues } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import {
  agentDescription,
  agentIcon,
} from '../agent-presentation.js';
import type { AgentOption, AgentType } from '../client.js';
import { LightDomElement } from './light-dom-element.js';

export interface ChatComposerActions {
  updateInput: (value: string) => void;
  chooseAgent: (agentType: AgentType) => void;
  send: () => void;
  stop: () => void;
}

@customElement('chat-composer')
export class ChatComposer extends LightDomElement {
  @property() input = '';
  @property({ type: Boolean }) busy = false;
  @property({ type: Boolean }) conversationActive = false;
  @property() agentType: AgentType = 'agent-framework';
  @property({ attribute: false }) agentOptions: AgentOption[] = [];
  @property({ attribute: false }) actions!: ChatComposerActions;
  private resizeObserver?: ResizeObserver;
  private observedTextareaWidth = 0;

  protected updated(changed: PropertyValues): void {
    if (changed.has('input')) this.resizeTextarea();
  }

  protected firstUpdated(): void {
    const textarea = this.querySelector<HTMLTextAreaElement>('#message-input');
    if (!textarea) return;
    this.resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry || Math.abs(entry.contentRect.width - this.observedTextareaWidth) < 0.5) {
        return;
      }
      this.observedTextareaWidth = entry.contentRect.width;
      this.resizeTextarea(textarea);
    });
    this.resizeObserver.observe(textarea);
  }

  disconnectedCallback(): void {
    this.resizeObserver?.disconnect();
    super.disconnectedCallback();
  }

  render() {
    const selectedAgent = this.agentOptions.find(
      (agent) => agent.agent_type === this.agentType,
    );
    const agentLabel = selectedAgent?.label ?? 'Conversation agent';
    const agentTitle = this.conversationActive
      ? `${agentLabel} is locked for this thread`
      : `${agentLabel} — ${agentDescription(this.agentType)}`;

    return html`
      <footer class="composer" aria-label="Message composer">
        <div class="composer-inner">
          <div class="composer-box">
            <textarea
              id="message-input"
              rows="3"
              aria-label="Message Memory Thread"
              aria-describedby="composer-shortcut"
              placeholder=${this.agentType === 'directive-rag'
                ? 'Ask about a company directive…'
                : 'Ask a support question…'}
              .value=${this.input}
              @input=${this.onInput}
              @keydown=${this.onKeydown}
            ></textarea>
            <div class="composer-toolbar">
              <div class="composer-selectors">
                <label class="composer-selector agent-selector" title=${agentTitle}>
                  <span class="material-symbols-outlined selector-leading" aria-hidden="true">
                    ${agentIcon(this.agentType)}
                  </span>
                  <span class="sr-only">Agent</span>
                  <select
                    class="composer-select agent-select"
                    aria-label="Conversation agent"
                    .value=${this.agentOptions.length ? this.agentType : ''}
                    ?disabled=${this.busy ||
                    this.conversationActive ||
                    this.agentOptions.length === 0}
                    @change=${this.onAgentSelect}
                  >
                    ${this.agentOptions.length
                      ? this.agentOptions.map(
                          (agent) => html`
                            <option value=${agent.agent_type}>${agent.label}</option>
                          `,
                        )
                      : html`<option value="">No agents available</option>`}
                  </select>
                  <span class="material-symbols-outlined selector-chevron" aria-hidden="true">
                    ${this.conversationActive ? 'lock' : 'expand_more'}
                  </span>
                </label>

                <label
                  class="composer-selector model-selector"
                  title="Model routing is not connected to the backend yet"
                >
                  <span class="material-symbols-outlined selector-leading" aria-hidden="true">
                    model_training
                  </span>
                  <span class="sr-only">Model</span>
                  <select
                    class="composer-select model-select"
                    aria-label="Model"
                    aria-describedby="model-routing-status"
                  >
                    <option value="agent-default">Agent default</option>
                    <option disabled>More models require backend routing</option>
                  </select>
                  <span class="material-symbols-outlined selector-chevron" aria-hidden="true">
                    expand_more
                  </span>
                </label>
              </div>

              <button
                class="send-button ${this.busy ? 'stop-button' : ''}"
                type="button"
                aria-label=${this.busy ? 'Stop response' : 'Send message'}
                title=${this.busy ? 'Stop' : 'Send'}
                ?disabled=${!this.busy
                  && (!this.input.trim() || this.agentOptions.length === 0)}
                @click=${this.busy ? this.actions.stop : this.actions.send}
              >
                <span class="material-symbols-outlined">
                  ${this.busy ? 'stop' : 'arrow_upward'}
                </span>
              </button>
            </div>
            <span id="composer-shortcut" class="sr-only">
              Press Enter to send. Press Shift and Enter for a new line.
            </span>
            <span id="model-routing-status" class="sr-only">
              Agent default is the only available model until backend routing is added.
            </span>
          </div>
        </div>
      </footer>
    `;
  }

  private onKeydown = (event: KeyboardEvent): void => {
    if (
      event.key !== 'Enter' ||
      event.shiftKey ||
      event.isComposing ||
      this.busy ||
      !this.input.trim() ||
      this.agentOptions.length === 0
    ) return;
    event.preventDefault();
    this.actions.send();
  };

  private onInput = (event: Event): void => {
    const textarea = event.currentTarget as HTMLTextAreaElement;
    this.actions.updateInput(textarea.value);
    this.resizeTextarea(textarea);
  };

  private onAgentSelect = (event: Event): void => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    const agent = this.agentOptions.find((option) => option.agent_type === value);
    if (agent) this.actions.chooseAgent(agent.agent_type);
  };

  private resizeTextarea(
    textarea = this.querySelector<HTMLTextAreaElement>('#message-input'),
  ): void {
    if (!textarea) return;
    textarea.style.height = 'auto';
    const maxHeight = Number.parseFloat(getComputedStyle(textarea).maxHeight);
    const height = Number.isFinite(maxHeight)
      ? Math.min(textarea.scrollHeight, maxHeight)
      : textarea.scrollHeight;
    textarea.style.height = `${Math.ceil(height)}px`;
    textarea.style.overflowY = textarea.scrollHeight > height ? 'auto' : 'hidden';
  }
}
