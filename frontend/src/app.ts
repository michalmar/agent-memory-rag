// app.ts — <a2ui-native-app> root component (Lit) for the vertical slice (§12).
import { LitElement, html, css, nothing, type PropertyValues } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

import './a2ui/surface-renderer.js';
import { A2UIProcessor, type SurfaceState } from './a2ui/processor.js';
import { AGUIClient, type AGUIEvent } from './client.js';
import { convertToolResult } from './converters.js';
import { getMockUserId, setMockUserId, getConfig } from './auth.js';
import { uiLogger } from './ui-logger.js';

interface ChatTurn {
  role: 'user' | 'assistant';
  text: string;
  surfaces: SurfaceState[];
  error?: string;
}

const MOCK_USERS = ['user-alice', 'user-bob', 'user-charlie'];

@customElement('a2ui-native-app')
export class NativeApp extends LitElement {
  @state() private turns: ChatTurn[] = [];
  @state() private input = '';
  @state() private ragMode: 'agentic' | 'none' = 'agentic';
  @state() private busy = false;
  @state() private mockUser = getMockUserId();
  @state() private theme: 'light' | 'dark' =
    (localStorage.getItem('theme') as 'light' | 'dark') || 'light';
  @state() private me: Record<string, unknown> | null = null;

  private client = new AGUIClient();
  private processor = new A2UIProcessor();
  private threadId: string | null = null;
  private toolNames = new Map<string, string>();
  private surfaceSeq = 0;

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      height: 100vh;
      color: var(--fg);
      background: var(--bg);
    }
    header {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 18px;
      border-bottom: 1px solid var(--border);
      background: var(--bg-alt);
    }
    header .title {
      font-weight: 700;
      font-size: 1rem;
      margin-right: auto;
    }
    header .sub {
      font-weight: 400;
      color: var(--fg-muted);
      font-size: 0.8rem;
      margin-left: 6px;
    }
    select,
    button.ctl {
      font: inherit;
      font-size: 0.82rem;
      padding: 5px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card);
      color: var(--fg);
      cursor: pointer;
    }
    button.ctl.active {
      background: var(--accent);
      color: var(--accent-fg);
      border-color: var(--accent);
    }
    main {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .msg {
      max-width: 720px;
      width: fit-content;
    }
    .msg.user {
      align-self: flex-end;
    }
    .bubble {
      padding: 10px 14px;
      border-radius: 14px;
      font-size: 0.92rem;
      line-height: 1.5;
    }
    .msg.user .bubble {
      background: var(--accent);
      color: var(--accent-fg);
      border-bottom-right-radius: 4px;
    }
    .msg.assistant .bubble {
      background: var(--bg-alt);
      border: 1px solid var(--border);
      border-bottom-left-radius: 4px;
    }
    .bubble :first-child { margin-top: 0; }
    .bubble :last-child { margin-bottom: 0; }
    .bubble pre {
      background: var(--card);
      padding: 8px 10px;
      border-radius: 8px;
      overflow-x: auto;
    }
    .surfaces {
      margin-top: 10px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .err {
      color: #b91c1c;
      font-size: 0.85rem;
      margin-top: 6px;
    }
    .empty {
      margin: auto;
      color: var(--fg-muted);
      text-align: center;
      max-width: 420px;
    }
    footer {
      display: flex;
      gap: 10px;
      padding: 12px 18px;
      border-top: 1px solid var(--border);
      background: var(--bg-alt);
    }
    textarea {
      flex: 1;
      resize: none;
      font: inherit;
      font-size: 0.92rem;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--card);
      color: var(--fg);
    }
    button.send {
      padding: 0 18px;
      border-radius: 10px;
      border: none;
      background: var(--accent);
      color: var(--accent-fg);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }
    button.send:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .material-symbols-outlined {
      font-family: 'Material Symbols Outlined';
      vertical-align: middle;
    }
  `;

  connectedCallback(): void {
    super.connectedCallback();
    this.applyTheme();
    this.refreshMe();
  }

  private applyTheme(): void {
    document.documentElement.setAttribute('data-theme', this.theme);
    localStorage.setItem('theme', this.theme);
  }

  private async refreshMe(): Promise<void> {
    try {
      this.me = await this.client.me();
    } catch (e) {
      uiLogger.error('me() failed', e);
      this.me = null;
    }
  }

  protected updated(changed: PropertyValues): void {
    if (changed.has('turns')) {
      const main = this.renderRoot.querySelector('main');
      if (main) main.scrollTop = main.scrollHeight;
    }
  }

  private renderMarkdown(text: string) {
    const raw = marked.parse(text, { async: false }) as string;
    return unsafeHTML(DOMPurify.sanitize(raw));
  }

  private onMockUserChange(e: Event): void {
    const id = (e.target as HTMLSelectElement).value;
    this.mockUser = id;
    setMockUserId(id);
    // New identity → fresh conversation.
    this.threadId = null;
    this.turns = [];
    this.refreshMe();
  }

  private toggleTheme(): void {
    this.theme = this.theme === 'light' ? 'dark' : 'light';
    this.applyTheme();
  }

  private toggleRag(): void {
    this.ragMode = this.ragMode === 'agentic' ? 'none' : 'agentic';
  }

  private async send(): Promise<void> {
    const text = this.input.trim();
    if (!text || this.busy) return;
    this.input = '';
    this.busy = true;

    const history = this.turns.map((t) => ({ role: t.role, content: t.text }));
    const userTurn: ChatTurn = { role: 'user', text, surfaces: [] };
    const assistantTurn: ChatTurn = { role: 'assistant', text: '', surfaces: [] };
    this.turns = [...this.turns, userTurn, assistantTurn];

    const messages = [...history, { role: 'user', content: text }];

    try {
      await this.client.chat(messages, this.threadId, this.ragMode, {
        onSessionId: (id) => (this.threadId = id),
        onEvent: (ev) => this.handleEvent(ev, assistantTurn),
      });
    } catch (e) {
      assistantTurn.error = String(e);
    } finally {
      this.busy = false;
      this.turns = [...this.turns];
    }
  }

  private handleEvent(ev: AGUIEvent, turn: ChatTurn): void {
    switch (ev.type) {
      case 'TEXT_MESSAGE_CONTENT':
        turn.text += ev.delta ?? '';
        break;
      case 'TOOL_CALL_START':
        if (ev.toolCallId && ev.toolCallName) {
          this.toolNames.set(ev.toolCallId, ev.toolCallName);
        }
        break;
      case 'TOOL_CALL_RESULT': {
        const name = ev.toolCallId ? this.toolNames.get(ev.toolCallId) : undefined;
        if (name && ev.content) this.renderToolSurface(name, ev.content, turn);
        break;
      }
      case 'RUN_ERROR':
        turn.error = ev.message ?? 'Run error';
        break;
      default:
        break;
    }
    this.turns = [...this.turns];
  }

  private renderToolSurface(toolName: string, content: string, turn: ChatTurn): void {
    const surfaceId = `s-${this.surfaceSeq++}`;
    const messages = convertToolResult(toolName, content, surfaceId);
    if (messages.length === 0) return;
    for (const m of messages) this.processor.apply(m);
    const surface = this.processor.getSurface(surfaceId);
    if (surface) turn.surfaces = [...turn.surfaces, surface];
  }

  private onKeydown(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void this.send();
    }
  }

  render() {
    const name = (this.me?.display_name as string) ?? this.mockUser;
    return html`
      <header>
        <span class="material-symbols-outlined">support_agent</span>
        <span class="title">
          Support Chat<span class="sub">signed in as ${name}</span>
        </span>
        <select
          .value=${this.mockUser}
          @change=${this.onMockUserChange}
          title="Switch mock user"
        >
          ${MOCK_USERS.map((u) => html`<option value=${u}>${u}</option>`)}
        </select>
        <button
          class="ctl ${this.ragMode === 'agentic' ? 'active' : ''}"
          @click=${this.toggleRag}
          title="Toggle knowledge-base RAG"
        >
          RAG: ${this.ragMode === 'agentic' ? 'on' : 'off'}
        </button>
        <button class="ctl" @click=${this.toggleTheme} title="Toggle theme">
          <span class="material-symbols-outlined">
            ${this.theme === 'light' ? 'dark_mode' : 'light_mode'}
          </span>
        </button>
      </header>

      <main>
        ${this.turns.length === 0
          ? html`<div class="empty">
              <p><strong>Ask about an order.</strong></p>
              <p>Try: “Where is my order ORD-001?”</p>
            </div>`
          : this.turns.map((t) => this.renderTurn(t))}
      </main>

      <footer>
        <textarea
          rows="1"
          placeholder="Type a message…"
          .value=${this.input}
          @input=${(e: Event) => (this.input = (e.target as HTMLTextAreaElement).value)}
          @keydown=${this.onKeydown}
        ></textarea>
        <button class="send" ?disabled=${this.busy || !this.input.trim()} @click=${this.send}>
          Send
        </button>
      </footer>
    `;
  }

  private renderTurn(t: ChatTurn) {
    return html`
      <div class="msg ${t.role}">
        <div class="bubble">
          ${t.role === 'assistant' ? this.renderMarkdown(t.text || '…') : t.text}
        </div>
        ${t.surfaces.length
          ? html`<div class="surfaces">
              ${t.surfaces.map(
                (s) => html`<a2ui-surface .surface=${s}></a2ui-surface>`,
              )}
            </div>`
          : nothing}
        ${t.error ? html`<div class="err">⚠ ${t.error}</div>` : nothing}
      </div>
    `;
  }
}

// Touch getConfig so tree-shaking keeps runtime config wiring available.
void getConfig();
