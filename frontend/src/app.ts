// app.ts — <a2ui-native-app> root component (Lit). Chat + memory-layer UI (§12):
// collapsible sidebar (History + Memory), Profile drawer, RAG toggle, A2UI surfaces.
import { LitElement, html, css, nothing, type PropertyValues } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

import './a2ui/surface-renderer.js';
import { A2UIProcessor, type SurfaceState } from './a2ui/processor.js';
import {
  AGUIClient,
  type AGUIEvent,
  type ConversationSummary,
  type MemoryRow,
  type ProfileDoc,
} from './client.js';
import { convertToolResult } from './converters.js';
import { getMockUserId, setMockUserId, getConfig, signOut } from './auth.js';
import { uiLogger } from './ui-logger.js';

interface ChatTurn {
  role: 'user' | 'assistant';
  text: string;
  surfaces: SurfaceState[];
  error?: string;
}

type RagMode = 'none' | 'agentic' | 'classic';

const MOCK_USERS = ['user-alice', 'user-bob', 'user-charlie'];

@customElement('a2ui-native-app')
export class NativeApp extends LitElement {
  @state() private turns: ChatTurn[] = [];
  @state() private input = '';
  @state() private ragMode: RagMode = 'classic';
  @state() private busy = false;
  @state() private mockUser = getMockUserId();
  @state() private theme: 'light' | 'dark' =
    (localStorage.getItem('theme') as 'light' | 'dark') || 'light';
  @state() private me: Record<string, unknown> | null = null;

  // Memory-layer UI state
  @state() private sidebarOpen = true;
  @state() private conversations: ConversationSummary[] = [];
  @state() private memories: MemoryRow[] = [];
  @state() private memoryQuery = '';
  @state() private searchResults: MemoryRow[] | null = null;
  @state() private selectedMemory: MemoryRow | null = null;
  @state() private memorisedIds = new Set<string>();
  @state() private profileOpen = false;
  @state() private profile: ProfileDoc | null = null;
  @state() private profileDraft = '';
  @state() private toast: string | null = null;

  private client = new AGUIClient();
  private processor = new A2UIProcessor();
  private threadId: string | null = null;
  private toolNames = new Map<string, string>();
  private surfaceSeq = 0;
  private toastTimer?: number;

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
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    button.ctl.active {
      background: var(--accent);
      color: var(--accent-fg);
      border-color: var(--accent);
    }
    button.icon-btn {
      border: none;
      background: transparent;
      color: var(--fg-muted);
      cursor: pointer;
      padding: 2px 4px;
      border-radius: 6px;
    }
    button.icon-btn:hover {
      background: var(--bg-alt);
      color: var(--fg);
    }
    /* ---------------- layout: sidebar + chat ---------------- */
    .body {
      flex: 1;
      display: flex;
      min-height: 0;
    }
    aside.sidebar {
      width: 288px;
      flex-shrink: 0;
      border-right: 1px solid var(--border);
      background: var(--bg-alt);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    aside.sidebar.collapsed {
      display: none;
    }
    .side-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 8px 10px;
    }
    .side-section-title {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--fg-muted);
      margin: 14px 4px 6px;
    }
    .side-section-title .count {
      margin-left: auto;
      font-weight: 500;
    }
    .list-item {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 7px 8px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 0.85rem;
    }
    .list-item:hover {
      background: var(--card);
    }
    .list-item.active {
      background: var(--card);
      border: 1px solid var(--border);
    }
    .list-item .li-main {
      flex: 1;
      min-width: 0;
    }
    .list-item .li-title {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .list-item .li-sub {
      font-size: 0.72rem;
      color: var(--fg-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .list-item .row-actions {
      display: none;
      gap: 2px;
    }
    .list-item:hover .row-actions {
      display: flex;
    }
    .side-empty {
      color: var(--fg-muted);
      font-size: 0.8rem;
      padding: 6px 8px;
    }
    .mem-search {
      display: flex;
      gap: 6px;
      padding: 2px 4px 6px;
    }
    .mem-search input {
      flex: 1;
      min-width: 0;
      font: inherit;
      font-size: 0.82rem;
      padding: 6px 8px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card);
      color: var(--fg);
    }
    .sim {
      font-size: 0.7rem;
      font-weight: 600;
      color: var(--accent);
    }
    .user-card {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border-top: 1px solid var(--border);
      cursor: pointer;
    }
    .user-card:hover {
      background: var(--card);
    }
    .avatar {
      width: 30px;
      height: 30px;
      border-radius: 50%;
      background: var(--accent);
      color: var(--accent-fg);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.75rem;
      font-weight: 700;
      flex-shrink: 0;
    }
    .chat-col {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
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
    /* ---------------- memory detail ---------------- */
    .mem-detail {
      max-width: 720px;
      margin: auto;
      background: var(--bg-alt);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px;
    }
    .mem-detail h2 {
      margin: 0 0 4px;
      font-size: 1.05rem;
    }
    .mem-detail .meta {
      color: var(--fg-muted);
      font-size: 0.78rem;
      margin-bottom: 14px;
    }
    footer {
      display: flex;
      gap: 10px;
      align-items: center;
      padding: 12px 18px;
      border-top: 1px solid var(--border);
      background: var(--bg-alt);
    }
    .rag-toggle {
      display: flex;
      gap: 4px;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 3px;
      background: var(--card);
      flex-shrink: 0;
    }
    .rag-toggle button {
      font: inherit;
      font-size: 0.76rem;
      padding: 4px 9px;
      border: none;
      border-radius: 7px;
      background: transparent;
      color: var(--fg-muted);
      cursor: pointer;
    }
    .rag-toggle button.on {
      background: var(--accent);
      color: var(--accent-fg);
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
      height: 40px;
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
    /* ---------------- profile drawer ---------------- */
    .drawer-scrim {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.35);
      z-index: 40;
    }
    .drawer {
      position: fixed;
      top: 0;
      right: 0;
      height: 100vh;
      width: 400px;
      max-width: 92vw;
      background: var(--bg);
      border-left: 1px solid var(--border);
      box-shadow: var(--shadow);
      z-index: 41;
      display: flex;
      flex-direction: column;
    }
    .drawer header {
      justify-content: space-between;
    }
    .drawer .drawer-body {
      flex: 1;
      overflow-y: auto;
      padding: 16px 18px;
    }
    .drawer textarea {
      width: 100%;
      min-height: 320px;
      font-family: ui-monospace, monospace;
      font-size: 0.8rem;
    }
    .drawer .actions {
      display: flex;
      gap: 8px;
      padding: 12px 18px;
      border-top: 1px solid var(--border);
    }
    .field-note {
      font-size: 0.78rem;
      color: var(--fg-muted);
      margin: 0 0 10px;
    }
    .toast {
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      background: var(--fg);
      color: var(--bg);
      padding: 9px 16px;
      border-radius: 10px;
      font-size: 0.84rem;
      z-index: 50;
      box-shadow: var(--shadow);
    }
    .material-symbols-outlined {
      font-family: 'Material Symbols Outlined';
      font-size: 1.1rem;
      vertical-align: middle;
    }
  `;

  connectedCallback(): void {
    super.connectedCallback();
    this.applyTheme();
    void this.refreshMe();
    void this.refreshConversations();
    void this.refreshMemories();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    if (this.toastTimer) clearTimeout(this.toastTimer);
  }

  private applyTheme(): void {
    document.documentElement.setAttribute('data-theme', this.theme);
    localStorage.setItem('theme', this.theme);
  }

  private showToast(msg: string): void {
    this.toast = msg;
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => (this.toast = null), 2600);
  }

  private async refreshMe(): Promise<void> {
    try {
      this.me = await this.client.me();
    } catch (e) {
      uiLogger.error('me() failed', e);
      this.me = null;
    }
  }

  private async refreshConversations(): Promise<void> {
    try {
      this.conversations = await this.client.listConversations();
    } catch (e) {
      uiLogger.error('listConversations failed', e);
    }
  }

  private async refreshMemories(): Promise<void> {
    try {
      this.memories = await this.client.listMemories();
    } catch (e) {
      uiLogger.error('listMemories failed', e);
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
    // New identity → fresh conversation + reload that user's memory layers.
    this.newChat();
    void this.refreshMe();
    void this.refreshConversations();
    void this.refreshMemories();
  }

  private toggleTheme(): void {
    this.theme = this.theme === 'light' ? 'dark' : 'light';
    this.applyTheme();
  }

  private setRag(mode: RagMode): void {
    this.ragMode = mode;
  }

  private newChat = (): void => {
    this.threadId = null;
    this.turns = [];
    this.selectedMemory = null;
  };

  // ---------------------------------------------------------------- history
  private async openConversation(id: string): Promise<void> {
    this.selectedMemory = null;
    try {
      const doc = await this.client.getConversation(id);
      this.threadId = doc.id;
      this.turns = (doc.messages ?? []).map((m) => ({
        role: m.role === 'user' ? 'user' : 'assistant',
        text: m.content,
        surfaces: [],
      }));
      const rag = doc.metadata?.rag_mode as RagMode | undefined;
      if (rag === 'none' || rag === 'agentic' || rag === 'classic') this.ragMode = rag;
    } catch (e) {
      uiLogger.error('openConversation failed', e);
      this.showToast('Could not open conversation');
    }
  }

  private async renameConversation(ev: Event, c: ConversationSummary): Promise<void> {
    ev.stopPropagation();
    const title = window.prompt('Rename conversation', c.title ?? '')?.trim();
    if (!title) return;
    try {
      await this.client.renameConversation(c.id, title);
      await this.refreshConversations();
    } catch {
      this.showToast('Rename failed');
    }
  }

  private async deleteConversation(ev: Event, c: ConversationSummary): Promise<void> {
    ev.stopPropagation();
    if (!window.confirm(`Delete "${c.title ?? c.id}"? This also removes its memories.`)) return;
    try {
      await this.client.deleteConversation(c.id);
      if (this.threadId === c.id) this.newChat();
      await this.refreshConversations();
      await this.refreshMemories();
    } catch {
      this.showToast('Delete failed');
    }
  }

  private async memorise(ev: Event, c: ConversationSummary): Promise<void> {
    ev.stopPropagation();
    this.memorisedIds = new Set(this.memorisedIds).add(c.id);
    try {
      await this.client.createMemory(c.id, c.title);
      await this.refreshMemories();
      this.showToast('Conversation memorised');
    } catch {
      const next = new Set(this.memorisedIds);
      next.delete(c.id);
      this.memorisedIds = next;
      this.showToast('Memorise failed');
    }
  }

  // ---------------------------------------------------------------- memory
  private runMemorySearch = async (): Promise<void> => {
    const q = this.memoryQuery.trim();
    if (!q) {
      this.searchResults = null;
      return;
    }
    try {
      this.searchResults = await this.client.searchMemories(q);
    } catch {
      this.showToast('Memory search failed');
    }
  };

  private clearMemorySearch = (): void => {
    this.memoryQuery = '';
    this.searchResults = null;
  };

  private async deleteMemory(ev: Event, m: MemoryRow): Promise<void> {
    ev.stopPropagation();
    try {
      await this.client.deleteMemory(m.id);
      if (this.selectedMemory?.id === m.id) this.selectedMemory = null;
      await this.refreshMemories();
      await this.runMemorySearch();
    } catch {
      this.showToast('Delete failed');
    }
  }

  // ---------------------------------------------------------------- profile
  private openProfile = async (): Promise<void> => {
    try {
      this.profile = await this.client.getProfile();
      this.profileDraft = JSON.stringify(this.profileToSections(this.profile), null, 2);
      this.profileOpen = true;
    } catch {
      this.showToast('Could not load profile');
    }
  };

  private profileToSections(p: ProfileDoc): Record<string, unknown> {
    return {
      basic_info: p.basic_info ?? {},
      interests: p.interests ?? [],
      habits: p.habits ?? [],
      preferences: p.preferences ?? {},
      status: p.status ?? {},
      facts: p.facts ?? [],
    };
  }

  private saveProfile = async (): Promise<void> => {
    let sections: Record<string, unknown>;
    try {
      sections = JSON.parse(this.profileDraft);
    } catch {
      this.showToast('Profile JSON is invalid');
      return;
    }
    try {
      this.profile = await this.client.putProfile(sections);
      this.profileDraft = JSON.stringify(this.profileToSections(this.profile), null, 2);
      this.showToast('Profile saved');
    } catch {
      this.showToast('Save failed');
    }
  };

  private generateProfile = async (): Promise<void> => {
    if (!this.threadId) {
      this.showToast('Open or start a conversation first');
      return;
    }
    try {
      const res = await this.client.generateProfile(this.threadId);
      if (res.updated && res.profile) {
        this.profile = res.profile;
        this.profileDraft = JSON.stringify(this.profileToSections(res.profile), null, 2);
        this.showToast('Profile generated from conversation');
      } else {
        this.showToast('No new profile facts found');
      }
    } catch {
      this.showToast('Generate failed');
    }
  };

  private clearProfile = async (): Promise<void> => {
    if (!window.confirm('Delete the entire profile for this user?')) return;
    try {
      await this.client.deleteProfile();
      this.profile = await this.client.getProfile();
      this.profileDraft = JSON.stringify(this.profileToSections(this.profile), null, 2);
      this.showToast('Profile cleared');
    } catch {
      this.showToast('Clear failed');
    }
  };

  // ---------------------------------------------------------------- chat
  private send = async (): Promise<void> => {
    const text = this.input.trim();
    if (!text || this.busy) return;
    this.input = '';
    this.busy = true;
    this.selectedMemory = null;

    const history = this.turns.map((t) => ({ role: t.role, content: t.text }));
    const userTurn: ChatTurn = { role: 'user', text, surfaces: [] };
    const assistantTurn: ChatTurn = { role: 'assistant', text: '', surfaces: [] };
    this.turns = [...this.turns, userTurn, assistantTurn];

    const messages = [...history, { role: 'user', content: text }];
    const isNew = this.threadId === null;

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
      // A turn was persisted server-side; refresh history (new convo appears / updates).
      void this.refreshConversations();
      if (isNew) void this.refreshMe();
    }
  };

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

  // ---------------------------------------------------------------- render
  render() {
    const name = (this.me?.display_name as string) ?? this.mockUser;
    const initials = (this.me?.initials as string) ?? this.mockUser.slice(5, 7).toUpperCase();
    return html`
      <header>
        <button class="icon-btn" @click=${() => (this.sidebarOpen = !this.sidebarOpen)} title="Toggle sidebar">
          <span class="material-symbols-outlined">menu</span>
        </button>
        <span class="material-symbols-outlined">support_agent</span>
        <span class="title">
          Support Chat<span class="sub">signed in as ${name}</span>
        </span>
        <button class="ctl" @click=${this.newChat} title="Start a new chat">
          <span class="material-symbols-outlined">add</span> New
        </button>
        ${getConfig().authMode === 'mock'
          ? html`<select .value=${this.mockUser} @change=${this.onMockUserChange} title="Switch mock user">
              ${MOCK_USERS.map((u) => html`<option value=${u} ?selected=${u === this.mockUser}>${u}</option>`)}
            </select>`
          : html`<button class="ctl" @click=${() => void signOut()} title="Sign out">
              <span class="material-symbols-outlined">logout</span> Sign out
            </button>`}
        <button class="ctl" @click=${this.toggleTheme} title="Toggle theme">
          <span class="material-symbols-outlined">
            ${this.theme === 'light' ? 'dark_mode' : 'light_mode'}
          </span>
        </button>
      </header>

      <div class="body">
        ${this.renderSidebar(initials, name)}
        <div class="chat-col">
          <main>${this.renderMain()}</main>
          <footer>
            <div class="rag-toggle" title="Knowledge-base retrieval mode">
              ${(['none', 'agentic', 'classic'] as RagMode[]).map(
                (m) => html`<button
                  class=${this.ragMode === m ? 'on' : ''}
                  @click=${() => this.setRag(m)}
                >
                  ${m === 'none' ? 'Off' : m[0].toUpperCase() + m.slice(1)}
                </button>`,
              )}
            </div>
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
        </div>
      </div>

      ${this.renderProfileDrawer()}
      ${this.toast ? html`<div class="toast">${this.toast}</div>` : nothing}
    `;
  }

  private renderSidebar(initials: string, name: string) {
    const mems = this.searchResults ?? this.memories;
    return html`
      <aside class="sidebar ${this.sidebarOpen ? '' : 'collapsed'}">
        <div class="side-scroll">
          <div class="side-section-title">
            <span class="material-symbols-outlined">history</span> History
            <span class="count">${this.conversations.length}</span>
          </div>
          ${this.conversations.length === 0
            ? html`<div class="side-empty">No conversations yet.</div>`
            : this.conversations.map((c) => this.renderConversation(c))}

          <div class="side-section-title">
            <span class="material-symbols-outlined">psychology</span> Memory
            <span class="count">${this.memories.length}</span>
          </div>
          <div class="mem-search">
            <input
              placeholder="Semantic search…"
              .value=${this.memoryQuery}
              @input=${(e: Event) => (this.memoryQuery = (e.target as HTMLInputElement).value)}
              @keydown=${(e: KeyboardEvent) => e.key === 'Enter' && this.runMemorySearch()}
            />
            <button class="icon-btn" @click=${this.runMemorySearch} title="Search">
              <span class="material-symbols-outlined">search</span>
            </button>
            ${this.searchResults
              ? html`<button class="icon-btn" @click=${this.clearMemorySearch} title="Clear">
                  <span class="material-symbols-outlined">close</span>
                </button>`
              : nothing}
          </div>
          ${mems.length === 0
            ? html`<div class="side-empty">
                ${this.searchResults ? 'No matches.' : 'No memories yet — use “Memorise”.'}
              </div>`
            : mems.map((m) => this.renderMemoryItem(m))}
        </div>

        <div class="user-card" @click=${this.openProfile} title="Open profile">
          <div class="avatar">${initials}</div>
          <div class="li-main">
            <div class="li-title">${name}</div>
            <div class="li-sub">View profile</div>
          </div>
          <span class="material-symbols-outlined">tune</span>
        </div>
      </aside>
    `;
  }

  private renderConversation(c: ConversationSummary) {
    const memorised = this.memorisedIds.has(c.id);
    return html`
      <div
        class="list-item ${this.threadId === c.id ? 'active' : ''}"
        @click=${() => this.openConversation(c.id)}
      >
        <div class="li-main">
          <div class="li-title">${c.title ?? 'Untitled'}</div>
          <div class="li-sub">${c.message_count ?? 0} msgs</div>
        </div>
        <div class="row-actions">
          <button
            class="icon-btn"
            title=${memorised ? 'Memorised' : 'Memorise'}
            @click=${(e: Event) => this.memorise(e, c)}
          >
            <span class="material-symbols-outlined">${memorised ? 'bookmark_added' : 'bookmark_add'}</span>
          </button>
          <button class="icon-btn" title="Rename" @click=${(e: Event) => this.renameConversation(e, c)}>
            <span class="material-symbols-outlined">edit</span>
          </button>
          <button class="icon-btn" title="Delete" @click=${(e: Event) => this.deleteConversation(e, c)}>
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
      </div>
    `;
  }

  private renderMemoryItem(m: MemoryRow) {
    return html`
      <div
        class="list-item ${this.selectedMemory?.id === m.id ? 'active' : ''}"
        @click=${() => (this.selectedMemory = m)}
      >
        <div class="li-main">
          <div class="li-title">${m.source_title ?? 'Memory'}</div>
          <div class="li-sub">${(m.summary ?? '').slice(0, 60)}…</div>
        </div>
        ${typeof m.similarity === 'number'
          ? html`<span class="sim">${Math.round(m.similarity * 100)}%</span>`
          : nothing}
        <div class="row-actions">
          <button class="icon-btn" title="Delete" @click=${(e: Event) => this.deleteMemory(e, m)}>
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
      </div>
    `;
  }

  private renderMain() {
    if (this.selectedMemory) return this.renderMemoryDetail(this.selectedMemory);
    if (this.turns.length === 0) {
      return html`<div class="empty">
        <p><strong>Ask about an order or our policies.</strong></p>
        <p>Try: “Where is my order ORD-001?” or “What's your return policy?”</p>
      </div>`;
    }
    return this.turns.map((t) => this.renderTurn(t));
  }

  private renderMemoryDetail(m: MemoryRow) {
    return html`
      <div class="mem-detail">
        <h2>${m.source_title ?? 'Memory'}</h2>
        <div class="meta">
          from conversation ${m.conversation_id}
          ${typeof m.similarity === 'number' ? `· ${Math.round(m.similarity * 100)}% match` : ''}
          ${m.created_at ? `· ${new Date(m.created_at).toLocaleString()}` : ''}
        </div>
        <p>${m.summary}</p>
        <div style="display:flex; gap:8px; margin-top:16px;">
          <button class="ctl" @click=${() => this.openConversation(m.conversation_id)}>
            Open conversation
          </button>
          <button class="ctl" @click=${() => (this.selectedMemory = null)}>Close</button>
        </div>
      </div>
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
              ${t.surfaces.map((s) => html`<a2ui-surface .surface=${s}></a2ui-surface>`)}
            </div>`
          : nothing}
        ${t.error ? html`<div class="err">⚠ ${t.error}</div>` : nothing}
      </div>
    `;
  }

  private renderProfileDrawer() {
    if (!this.profileOpen) return nothing;
    const version = this.profile?.version ?? 0;
    return html`
      <div class="drawer-scrim" @click=${() => (this.profileOpen = false)}></div>
      <div class="drawer">
        <header>
          <span class="title">User Profile <span class="sub">v${version}</span></span>
          <button class="icon-btn" @click=${() => (this.profileOpen = false)} title="Close">
            <span class="material-symbols-outlined">close</span>
          </button>
        </header>
        <div class="drawer-body">
          <p class="field-note">
            Learned automatically during chat, or generate from the current conversation.
            Edit the JSON sections directly and save.
          </p>
          <textarea
            .value=${this.profileDraft}
            @input=${(e: Event) => (this.profileDraft = (e.target as HTMLTextAreaElement).value)}
          ></textarea>
        </div>
        <div class="actions">
          <button class="send" @click=${this.saveProfile}>Save</button>
          <button class="ctl" @click=${this.generateProfile} title="Extract from current conversation">
            Generate
          </button>
          <button class="ctl" style="margin-left:auto" @click=${this.clearProfile}>Clear</button>
        </div>
      </div>
    `;
  }
}

// Touch getConfig so tree-shaking keeps runtime config wiring available.
void getConfig();
