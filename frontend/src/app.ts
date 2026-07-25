// app.ts — <a2ui-native-app> root component (Lit). Chat + memory-layer UI (§12):
// collapsible sidebar, selectable Foundry agents, memory/profile UI, and A2UI surfaces.
import { LitElement, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators.js';

import type { ChatTurn, ResourceStatus } from './chat-models.js';
import {
  createChatEventState,
  reduceChatEvent,
} from './chat-event-reducer.js';
import {
  findCitationByIdentity,
  replaceCitationMarkers,
} from './citations.js';
import {
  AGUIClient,
  type AgentOption,
  type AgentType,
  type ConversationSummary,
  type DirectiveDocument,
  type DirectiveSourceItem,
  DirectiveSourceRequestError,
  type MemoryRow,
  type ProfileDoc,
} from './client.js';
import {
  isAbortError,
  LatestRequest,
  pdfUrlForPage,
  type DirectiveDocumentLoadStatus,
  type DirectiveDocumentReference,
  type DirectiveDocumentTab,
} from './directive-documents.js';
import {
  AUTH_REQUIRED_EVENT,
  type AuthSession,
  getMockUserId,
  getConfig,
  initializeAuthSession,
  setMockUserId,
  signIn,
  signOut,
} from './auth.js';
import { appStyles } from './app.styles.js';
import './components/chat-composer.js';
import './components/chat-transcript.js';
import './components/conversation-sidebar.js';
import './components/directive-document-viewer.js';
import './components/memory-rail.js';
import './components/profile-drawer.js';
import './components/source-documents-rail.js';
import type { SourceDocumentsRailActions } from './components/source-documents-rail.js';
import {
  directiveSourceUploadError,
  type DirectiveSourceUploadStatus,
  validateDirectiveSourceFilename,
} from './source-documents.js';
import { uiLogger } from './ui-logger.js';

type AuthStatus = 'checking' | 'signed-out' | 'signing-in' | 'signed-in' | 'error';
type RightRailMode = 'memory' | 'sources';

const storedTheme = localStorage.getItem('theme');
const INITIAL_THEME: 'light' | 'dark' = storedTheme === 'dark' ? 'dark' : 'light';
const THREAD_RAIL_DESKTOP = window.matchMedia('(min-width: 901px)');
const MEMORY_RAIL_DESKTOP = window.matchMedia('(min-width: 1361px)');
const REPOSITORY_URL = 'https://github.com/michalmar/agent-memory-rag';

@customElement('a2ui-native-app')
export class NativeApp extends LitElement {
  @state() private turns: ChatTurn[] = [];
  @state() private input = '';
  @state() private agentType: AgentType = 'agent-framework';
  @state() private agentOptions: AgentOption[] = [];
  @state() private busy = false;
  @state() private mockUser = getMockUserId();
  @state() private theme: 'light' | 'dark' = INITIAL_THEME;
  @state() private me: Record<string, unknown> | null = null;
  @state() private authStatus: AuthStatus =
    getConfig().authMode === 'mock' ? 'signed-in' : 'checking';
  @state() private authSession: AuthSession | null = null;
  @state() private authError: string | null = null;

  // Memory-layer UI state
  @state() private sidebarOpen = THREAD_RAIL_DESKTOP.matches;
  @state() private memoryPanelOpen = MEMORY_RAIL_DESKTOP.matches;
  @state() private rightRailMode: RightRailMode = 'memory';
  @state() private conversations: ConversationSummary[] = [];
  @state() private memories: MemoryRow[] = [];
  @state() private conversationsStatus: ResourceStatus = 'loading';
  @state() private memoriesStatus: ResourceStatus = 'loading';
  @state() private memoryQuery = '';
  @state() private searchResults: MemoryRow[] | null = null;
  @state() private selectedMemory: MemoryRow | null = null;
  @state() private directiveSources: DirectiveSourceItem[] = [];
  @state() private directiveSourcesStatus: ResourceStatus = 'loading';
  @state() private directiveSourcesNextCursor: string | null = null;
  @state() private directiveSourceListLoading = false;
  @state() private directiveSourceUploadStatus:
    DirectiveSourceUploadStatus = 'idle';
  @state() private directiveSourceUploadProgress = 0;
  @state() private directiveSourceUploadError = '';
  @state() private directiveSourceDeletingFilename: string | null = null;
  @state() private directiveSourceDeleting = false;
  @state() private directiveSourceDeleteError = '';
  @state() private memorisedIds = new Set<string>();
  @state() private profileOpen = false;
  @state() private profile: ProfileDoc | null = null;
  @state() private profileDraft = '';
  @state() private directiveDocumentReference:
    DirectiveDocumentReference | null = null;
  @state() private directiveDocument: DirectiveDocument | null = null;
  @state() private directiveDocumentStatus:
    DirectiveDocumentLoadStatus = 'idle';
  @state() private directiveDocumentError = '';
  @state() private directiveDocumentTab: DirectiveDocumentTab = 'document';
  @state() private directivePdfStatus: DirectiveDocumentLoadStatus = 'idle';
  @state() private directivePdfError = '';
  @state() private directivePdfUrl: string | null = null;
  @state() private toast: string | null = null;

  private client = new AGUIClient();
  private conversationId: string | null = null;
  private surfaceSeq = 0;
  private toastTimer?: number;
  private profileTrigger?: HTMLElement;
  private directiveDocumentTrigger?: HTMLElement;
  private directiveDocumentAbort?: AbortController;
  private directivePdfAbort?: AbortController;
  private directivePdfObjectUrl?: string;
  private directiveDocumentRequests = new LatestRequest();
  private directivePdfRequests = new LatestRequest();
  private profileLoadGeneration = 0;
  private authGeneration = 0;
  private identityGeneration = 0;
  private directiveSourceListGeneration = 0;
  private chatGeneration = 0;
  private turnSequence = 0;
  private chatAbort?: AbortController;
  private sidebarActions = {
    close: () => (this.sidebarOpen = false),
    newConversation: () => this.newChat(),
    toggleMemory: () => this.toggleMemoryPanel(),
    openProfile: (trigger?: HTMLElement) => void this.openProfile(trigger),
    switchMockUser: (userId: string) => this.switchMockUser(userId),
    signOut: () => void this.signOutOfApplication(),
    openConversation: (conversationId: string) =>
      void this.openConversation(conversationId),
    saveMemory: (conversation: ConversationSummary) =>
      void this.memorise(conversation),
    renameConversation: (conversation: ConversationSummary) =>
      void this.renameConversation(conversation),
    deleteConversation: (conversation: ConversationSummary) =>
      void this.deleteConversation(conversation),
  };
  private memoryActions = {
    close: () => (this.memoryPanelOpen = false),
    updateQuery: (query: string) => (this.memoryQuery = query),
    search: () => void this.runMemorySearch(),
    clearSearch: () => this.clearMemorySearch(),
    select: (memory: MemoryRow) => this.selectMemory(memory),
    clearSelection: () => (this.selectedMemory = null),
    openConversation: (conversationId: string) =>
      this.openConversationFromMemory(conversationId),
    deleteMemory: (memory: MemoryRow) => void this.deleteMemory(memory),
  };
  private sourceDocumentsActions: SourceDocumentsRailActions = {
    close: () => (this.memoryPanelOpen = false),
    upload: (file: File) => void this.uploadDirectiveSource(file),
    loadMore: () => void this.loadMoreDirectiveSources(),
    retry: () => void this.refreshDirectiveSources(),
    requestDelete: (filename: string) => {
      this.directiveSourceDeletingFilename = filename;
      this.directiveSourceDeleteError = '';
    },
    cancelDelete: () => {
      this.directiveSourceDeletingFilename = null;
      this.directiveSourceDeleteError = '';
    },
    confirmDelete: (filename: string) =>
      void this.deleteDirectiveSource(filename),
  };
  private composerActions = {
    updateInput: (value: string) => (this.input = value),
    chooseAgent: (agentType: AgentType) => this.chooseAgent(agentType),
    send: () => void this.send(),
    stop: () => this.stopActiveChat(),
  };
  private transcriptActions = {
    copy: (turn: ChatTurn) => void this.copyTurn(turn),
    setFeedback: (turn: ChatTurn, feedback: 'up' | 'down') =>
      this.setFeedback(turn, feedback),
    openDocument: (
      reference: DirectiveDocumentReference,
      trigger?: HTMLElement,
    ) => this.openDirectiveDocument(reference, trigger),
  };
  private profileActions = {
    close: () => this.closeProfile(),
    updateDraft: (value: string) => (this.profileDraft = value),
    save: () => void this.saveProfile(),
    generate: () => void this.generateProfile(),
    clear: () => void this.clearProfile(),
  };
  private directiveDocumentActions = {
    close: () => this.closeDirectiveDocument(),
    selectTab: (tab: DirectiveDocumentTab) =>
      this.selectDirectiveDocumentTab(tab),
    openLinkedDocument: (
      reference: DirectiveDocumentReference,
      trigger?: HTMLElement,
    ) => this.openDirectiveDocument(reference, trigger),
    retryDocument: () => void this.loadDirectiveDocument(),
    retryPdf: () => void this.loadDirectivePdf(),
  };
  private onThreadRailBreakpointChange = (event: MediaQueryListEvent): void => {
    this.sidebarOpen = event.matches;
    if (!event.matches) this.memoryPanelOpen = false;
  };
  private onMemoryRailBreakpointChange = (event: MediaQueryListEvent): void => {
    this.memoryPanelOpen = event.matches;
  };
  private onAuthRequired = (): void => {
    if (getConfig().authMode !== 'entra' || this.authStatus !== 'signed-in') return;
    ++this.authGeneration;
    ++this.identityGeneration;
    this.cancelActiveChat();
    this.clearUserScopedState();
    this.authSession = null;
    this.authError = 'Your session expired. Sign in again to continue.';
    this.authStatus = 'signed-out';
  };

  static styles = appStyles;

  connectedCallback(): void {
    super.connectedCallback();
    THREAD_RAIL_DESKTOP.addEventListener('change', this.onThreadRailBreakpointChange);
    MEMORY_RAIL_DESKTOP.addEventListener('change', this.onMemoryRailBreakpointChange);
    window.addEventListener(AUTH_REQUIRED_EVENT, this.onAuthRequired);
    this.applyTheme();
    if (getConfig().authMode === 'entra') {
      void this.initializeAuthentication();
    } else {
      this.loadAuthenticatedState();
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    THREAD_RAIL_DESKTOP.removeEventListener('change', this.onThreadRailBreakpointChange);
    MEMORY_RAIL_DESKTOP.removeEventListener('change', this.onMemoryRailBreakpointChange);
    window.removeEventListener(AUTH_REQUIRED_EVENT, this.onAuthRequired);
    ++this.authGeneration;
    this.chatAbort?.abort();
    this.closeDirectiveDocument(false);
    if (this.toastTimer) clearTimeout(this.toastTimer);
  }

  private applyTheme(): void {
    document.documentElement.setAttribute('data-theme', this.theme);
    localStorage.setItem('theme', this.theme);
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', this.theme === 'dark' ? '#0E0F10' : '#F6F7F8');
  }

  private showToast(msg: string): void {
    this.toast = msg;
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => (this.toast = null), 2600);
  }

  private loadAuthenticatedState(): void {
    const generation = ++this.identityGeneration;
    void Promise.all([
      this.refreshMe(generation),
      this.refreshAgentOptions(generation),
      this.refreshConversations(generation),
      this.refreshMemories(generation),
    ]);
  }

  private async initializeAuthentication(): Promise<void> {
    const generation = ++this.authGeneration;
    this.authStatus = 'checking';
    this.authError = null;
    try {
      const session = await initializeAuthSession();
      if (generation !== this.authGeneration || !this.isConnected) return;
      if (!session) {
        this.authSession = null;
        this.authStatus = 'signed-out';
        return;
      }
      this.authSession = session;
      this.authStatus = 'signed-in';
      this.loadAuthenticatedState();
    } catch (error) {
      uiLogger.error('Authentication initialization failed', error);
      if (generation !== this.authGeneration || !this.isConnected) return;
      this.authSession = null;
      this.authError = 'Authentication could not be initialized. Check the Entra configuration and retry.';
      this.authStatus = 'error';
    }
  }

  private signInToApplication = async (): Promise<void> => {
    if (this.authStatus === 'signing-in') return;
    const generation = ++this.authGeneration;
    this.authStatus = 'signing-in';
    this.authError = null;
    try {
      const session = await signIn();
      if (generation !== this.authGeneration || !this.isConnected) return;
      this.authSession = session;
      this.authStatus = 'signed-in';
      this.loadAuthenticatedState();
    } catch (error) {
      uiLogger.error('Interactive sign-in failed', error);
      if (generation !== this.authGeneration || !this.isConnected) return;
      this.authSession = null;
      this.authError = 'Sign-in did not complete. Close any blocked popup and try again.';
      this.authStatus = 'signed-out';
    }
  };

  private signOutOfApplication = async (): Promise<void> => {
    try {
      await signOut();
      ++this.authGeneration;
      ++this.identityGeneration;
      this.cancelActiveChat();
      this.clearUserScopedState();
      this.authSession = null;
      this.authError = null;
      this.authStatus = 'signed-out';
    } catch (error) {
      uiLogger.error('Sign-out failed', error);
      this.showToast('Sign-out did not complete. Try again.');
    }
  };

  private async refreshMe(generation = this.identityGeneration): Promise<void> {
    try {
      const me = await this.client.me();
      if (generation !== this.identityGeneration) return;
      const gainedSourceAccess = (
        this.me?.can_manage_directive_sources !== true
        && me.can_manage_directive_sources === true
      );
      this.me = me;
      if (gainedSourceAccess) {
        await this.refreshDirectiveSources(generation);
      } else if (me.can_manage_directive_sources !== true) {
        this.clearDirectiveSourceState();
      }
    } catch (e) {
      uiLogger.error('me() failed', e);
      if (generation === this.identityGeneration) this.me = null;
    }
  }

  private async refreshConversations(generation = this.identityGeneration): Promise<void> {
    if (generation === this.identityGeneration && this.conversations.length === 0) {
      this.conversationsStatus = 'loading';
    }
    try {
      const conversations = await this.client.listConversations();
      if (generation === this.identityGeneration) {
        this.conversations = conversations;
        this.conversationsStatus = 'ready';
      }
    } catch (e) {
      uiLogger.error('listConversations failed', e);
      if (generation === this.identityGeneration) {
        this.conversations = [];
        this.conversationsStatus = 'error';
      }
    }
  }

  private async refreshAgentOptions(generation = this.identityGeneration): Promise<void> {
    try {
      const capabilities = await this.client.getAgentCapabilities();
      if (generation !== this.identityGeneration) return;
      this.agentOptions = capabilities.agents.filter((agent) => agent.available);
      if (!this.agentOptions.some((agent) => agent.agent_type === this.agentType)) {
        const first = this.agentOptions[0];
        if (first) this.agentType = first.agent_type;
      }
    } catch (e) {
      uiLogger.error('getAgentCapabilities failed', e);
      if (generation === this.identityGeneration) this.agentOptions = [];
    }
  }

  private async refreshMemories(generation = this.identityGeneration): Promise<void> {
    if (generation === this.identityGeneration && this.memories.length === 0) {
      this.memoriesStatus = 'loading';
    }
    try {
      const memories = await this.client.listMemories();
      if (generation === this.identityGeneration) {
        this.memories = memories;
        this.memoriesStatus = 'ready';
      }
    } catch (e) {
      uiLogger.error('listMemories failed', e);
      if (generation === this.identityGeneration) {
        this.memories = [];
        this.memoriesStatus = 'error';
      }
    }
  }

  private async refreshDirectiveSources(
    generation = this.identityGeneration,
    cursor: string | null = null,
    append = false,
  ): Promise<void> {
    if (!this.canManageDirectiveSources) return;
    const requestGeneration = ++this.directiveSourceListGeneration;
    this.directiveSourceListLoading = true;
    if (!append && generation === this.identityGeneration) {
      this.directiveSourcesStatus = 'loading';
    }
    try {
      const page = await this.client.listDirectiveSources(cursor);
      if (
        generation !== this.identityGeneration
        || requestGeneration !== this.directiveSourceListGeneration
      ) return;
      this.directiveSources = append
        ? [
            ...this.directiveSources,
            ...page.items.filter(
              (item) => !this.directiveSources.some(
                (current) => current.filename === item.filename,
              ),
            ),
          ]
        : page.items;
      this.directiveSourcesNextCursor = page.next_cursor ?? null;
      this.directiveSourcesStatus = 'ready';
    } catch (error) {
      uiLogger.error('listDirectiveSources failed', error);
      if (
        generation !== this.identityGeneration
        || requestGeneration !== this.directiveSourceListGeneration
      ) return;
      if (!append) this.directiveSources = [];
      this.directiveSourcesStatus = 'error';
    } finally {
      if (
        generation === this.identityGeneration
        && requestGeneration === this.directiveSourceListGeneration
      ) {
        this.directiveSourceListLoading = false;
      }
    }
  }

  private async loadMoreDirectiveSources(): Promise<void> {
    if (
      !this.directiveSourcesNextCursor
      || this.directiveSourceListLoading
    ) return;
    await this.refreshDirectiveSources(
      this.identityGeneration,
      this.directiveSourcesNextCursor,
      true,
    );
  }

  private async uploadDirectiveSource(file: File): Promise<void> {
    if (!this.canManageDirectiveSources) return;
    if (!validateDirectiveSourceFilename(file.name)) {
      this.directiveSourceUploadStatus = 'invalid';
      this.directiveSourceUploadError = (
        'Use the filename format 12345678-policy-name-v1.pdf.'
      );
      return;
    }
    const generation = this.identityGeneration;
    this.directiveSourceUploadStatus = 'uploading';
    this.directiveSourceUploadProgress = 0;
    this.directiveSourceUploadError = '';
    try {
      await this.client.uploadDirectiveSource(file, (progress) => {
        if (generation === this.identityGeneration) {
          this.directiveSourceUploadProgress = progress;
        }
      });
      if (generation !== this.identityGeneration) return;
      this.directiveSourceUploadStatus = 'idle';
      this.directiveSourceUploadProgress = 0;
      await this.refreshDirectiveSources(generation);
      this.showToast('Directive source uploaded');
    } catch (error) {
      uiLogger.error('uploadDirectiveSource failed', error);
      if (generation !== this.identityGeneration) return;
      const status = error instanceof DirectiveSourceRequestError
        ? error.status
        : 0;
      const uploadError = directiveSourceUploadError(status, file.name);
      this.directiveSourceUploadStatus = uploadError.status;
      this.directiveSourceUploadError = uploadError.message;
    }
  }

  private async deleteDirectiveSource(filename: string): Promise<void> {
    if (!this.canManageDirectiveSources || this.directiveSourceDeleting) return;
    const generation = this.identityGeneration;
    this.directiveSourceDeleting = true;
    this.directiveSourceDeleteError = '';
    try {
      await this.client.deleteDirectiveSource(filename);
      if (generation !== this.identityGeneration) return;
      this.directiveSourceDeletingFilename = null;
      await this.refreshDirectiveSources(generation);
      this.showToast('Directive source deleted');
    } catch (error) {
      uiLogger.error('deleteDirectiveSource failed', error);
      if (generation === this.identityGeneration) {
        this.directiveSourceDeleteError = (
          'The source PDF could not be deleted. Try again.'
        );
      }
    } finally {
      if (generation === this.identityGeneration) {
        this.directiveSourceDeleting = false;
      }
    }
  }

  private nextTurnId(): string {
    this.turnSequence += 1;
    return `turn-${this.turnSequence}`;
  }

  private copyTurn = async (turn: ChatTurn): Promise<void> => {
    if (!navigator.clipboard) {
      this.showToast('Clipboard access is unavailable');
      return;
    }
    try {
      const text = replaceCitationMarkers(turn.text, (marker) => {
        const index = findCitationByIdentity(turn.citations, marker);
        return index >= 0 ? `[${index + 1}]` : `[${marker.sourceName}]`;
      });
      await navigator.clipboard.writeText(text);
      this.showToast('Message copied');
    } catch (error) {
      uiLogger.error('copy message failed', error);
      this.showToast('Message could not be copied');
    }
  };

  private setFeedback(turn: ChatTurn, feedback: 'up' | 'down'): void {
    turn.feedback = turn.feedback === feedback ? undefined : feedback;
    this.turns = [...this.turns];
  }

  private switchMockUser(userId: string): void {
    if (userId === this.mockUser) return;

    const generation = ++this.identityGeneration;
    this.cancelActiveChat();
    this.mockUser = userId;
    setMockUserId(userId);
    this.clearUserScopedState();
    void Promise.all([
      this.refreshMe(generation),
      this.refreshAgentOptions(generation),
      this.refreshConversations(generation),
      this.refreshMemories(generation),
    ]);
  }

  private clearUserScopedState(): void {
    ++this.profileLoadGeneration;
    this.closeDirectiveDocument(false);
    this.conversationId = null;
    this.turns = [];
    this.conversations = [];
    this.agentOptions = [];
    this.memories = [];
    this.clearDirectiveSourceState();
    this.conversationsStatus = 'loading';
    this.memoriesStatus = 'loading';
    this.memoryQuery = '';
    this.searchResults = null;
    this.selectedMemory = null;
    this.memorisedIds = new Set();
    this.profileOpen = false;
    this.profile = null;
    this.profileDraft = '';
    this.me = null;
    this.busy = false;
  }

  private clearDirectiveSourceState(): void {
    ++this.directiveSourceListGeneration;
    this.directiveSources = [];
    this.directiveSourcesStatus = 'loading';
    this.directiveSourcesNextCursor = null;
    this.directiveSourceListLoading = false;
    this.directiveSourceUploadStatus = 'idle';
    this.directiveSourceUploadProgress = 0;
    this.directiveSourceUploadError = '';
    this.directiveSourceDeletingFilename = null;
    this.directiveSourceDeleting = false;
    this.directiveSourceDeleteError = '';
    if (this.rightRailMode === 'sources') {
      this.rightRailMode = 'memory';
      this.memoryPanelOpen = false;
    }
  }

  private toggleTheme(): void {
    this.theme = this.theme === 'light' ? 'dark' : 'light';
    this.applyTheme();
  }

  private chooseAgent(next: AgentType): void {
    if (next === this.agentType) return;
    if (this.conversationId && !window.confirm('Changing the runtime starts a new thread. Continue?')) {
      this.requestUpdate();
      return;
    }
    if (this.conversationId) this.newChat();
    this.agentType = next;
  }

  private cancelActiveChat(): number {
    const generation = ++this.chatGeneration;
    this.chatAbort?.abort();
    this.chatAbort = undefined;
    this.busy = false;
    return generation;
  }

  private stopActiveChat(): void {
    if (!this.busy) return;
    const activeTurn = this.turns.at(-1);
    if (activeTurn?.role === 'assistant') {
      this.replaceTurn({
        ...activeTurn,
        progress: {
          stage: activeTurn.progress?.stage ?? 'preparing_answer',
          status: 'cancelled',
          message: 'Response stopped',
        },
      });
    }
    this.cancelActiveChat();
  }

  private newChat = (): void => {
    this.cancelActiveChat();
    this.conversationId = null;
    this.turns = [];
    this.selectedMemory = null;
    if (!THREAD_RAIL_DESKTOP.matches) this.sidebarOpen = false;
  };

  private toggleSidebar(): void {
    const open = !this.sidebarOpen;
    this.sidebarOpen = open;
    if (open && !THREAD_RAIL_DESKTOP.matches) this.memoryPanelOpen = false;
  }

  private toggleMemoryPanel(): void {
    const closing = this.memoryPanelOpen && this.rightRailMode === 'memory';
    this.rightRailMode = 'memory';
    this.memoryPanelOpen = !closing;
    if (!closing && !THREAD_RAIL_DESKTOP.matches) this.sidebarOpen = false;
  }

  private toggleSourceDocumentsPanel(): void {
    const closing = this.memoryPanelOpen && this.rightRailMode === 'sources';
    this.rightRailMode = 'sources';
    this.memoryPanelOpen = !closing;
    if (!closing && !THREAD_RAIL_DESKTOP.matches) this.sidebarOpen = false;
  }

  private selectMemory(memory: MemoryRow): void {
    this.selectedMemory = memory;
    this.rightRailMode = 'memory';
    if (!MEMORY_RAIL_DESKTOP.matches) this.memoryPanelOpen = true;
    if (!THREAD_RAIL_DESKTOP.matches) this.sidebarOpen = false;
  }

  private openConversationFromMemory(id: string): void {
    void this.openConversation(id);
    if (!MEMORY_RAIL_DESKTOP.matches) this.memoryPanelOpen = false;
  }

  private onAppKeydown(e: KeyboardEvent): void {
    if (e.key !== 'Escape') return;
    if (this.directiveDocumentReference) {
      this.closeDirectiveDocument();
      return;
    }
    if (this.profileOpen) {
      this.closeProfile();
      return;
    }
    if (this.memoryPanelOpen && !MEMORY_RAIL_DESKTOP.matches) {
      this.memoryPanelOpen = false;
      return;
    }
    if (this.sidebarOpen && !THREAD_RAIL_DESKTOP.matches) {
      this.sidebarOpen = false;
    }
  }

  // ----------------------------------------------------- directive documents
  private openDirectiveDocument(
    reference: DirectiveDocumentReference,
    trigger?: HTMLElement,
  ): void {
    if (
      this.directiveDocumentReference?.directiveId === reference.directiveId
      && this.directiveDocumentReference.directiveVersionId
        === reference.directiveVersionId
    ) {
      this.selectDirectiveDocumentTab('pdf');
      requestAnimationFrame(() => {
        this.renderRoot
          .querySelector<HTMLElement>('#directive-pdf-tab')
          ?.focus();
      });
      return;
    }
    const returnTrigger = this.directiveDocumentTrigger ?? trigger;
    ++this.profileLoadGeneration;
    this.closeDirectiveDocument(false);
    const activeElement = this.shadowRoot?.activeElement;
    this.directiveDocumentTrigger =
      returnTrigger
      ?? (activeElement instanceof HTMLElement ? activeElement : undefined);
    this.directiveDocumentReference = reference;
    this.directiveDocument = null;
    this.directiveDocumentStatus = 'loading';
    this.directiveDocumentError = '';
    this.directiveDocumentTab = 'document';
    this.directivePdfStatus = 'idle';
    this.directivePdfError = '';
    this.profileOpen = false;
    void this.loadDirectiveDocument();
  }

  private async loadDirectiveDocument(): Promise<void> {
    const reference = this.directiveDocumentReference;
    if (!reference) return;

    const request = this.directiveDocumentRequests.begin();
    this.directiveDocumentAbort?.abort();
    const controller = new AbortController();
    this.directiveDocumentAbort = controller;
    this.directiveDocumentStatus = 'loading';
    this.directiveDocumentError = '';
    try {
      const document = await this.client.getDirectiveDocument(
        reference.directiveId,
        reference.directiveVersionId,
        controller.signal,
      );
      if (!this.directiveDocumentRequests.isCurrent(request)) return;
      this.directiveDocument = document;
      this.directiveDocumentStatus = 'ready';
    } catch (error) {
      if (
        !this.directiveDocumentRequests.isCurrent(request)
        || isAbortError(error)
      ) return;
      uiLogger.error('directive document load failed', error);
      this.directiveDocumentStatus = 'error';
      this.directiveDocumentError =
        'The published directive document is temporarily unavailable.';
    } finally {
      if (this.directiveDocumentRequests.isCurrent(request)) {
        this.directiveDocumentAbort = undefined;
      }
    }
  }

  private selectDirectiveDocumentTab(tab: DirectiveDocumentTab): void {
    this.directiveDocumentTab = tab;
    if (tab === 'pdf' && this.directivePdfStatus === 'idle') {
      void this.loadDirectivePdf();
    }
  }

  private async loadDirectivePdf(): Promise<void> {
    const reference = this.directiveDocumentReference;
    if (!reference) return;

    const request = this.directivePdfRequests.begin();
    this.directivePdfAbort?.abort();
    const controller = new AbortController();
    this.directivePdfAbort = controller;
    this.revokeDirectivePdfUrl();
    this.directivePdfStatus = 'loading';
    this.directivePdfError = '';
    try {
      const pdf = await this.client.getDirectiveSourcePdf(
        reference.directiveId,
        reference.directiveVersionId,
        controller.signal,
      );
      if (!this.directivePdfRequests.isCurrent(request)) return;
      const objectUrl = URL.createObjectURL(pdf);
      this.directivePdfObjectUrl = objectUrl;
      this.directivePdfUrl = pdfUrlForPage(objectUrl, reference.pageFrom);
      this.directivePdfStatus = 'ready';
    } catch (error) {
      if (
        !this.directivePdfRequests.isCurrent(request)
        || isAbortError(error)
      ) return;
      uiLogger.error('directive PDF load failed', error);
      this.directivePdfStatus = 'error';
      this.directivePdfError =
        'The original PDF is temporarily unavailable.';
    } finally {
      if (this.directivePdfRequests.isCurrent(request)) {
        this.directivePdfAbort = undefined;
      }
    }
  }

  private closeDirectiveDocument(restoreFocus = true): void {
    const trigger = this.directiveDocumentTrigger;
    this.directiveDocumentRequests.invalidate();
    this.directivePdfRequests.invalidate();
    this.abortDirectiveDocumentRequests();
    this.revokeDirectivePdfUrl();
    this.directiveDocumentReference = null;
    this.directiveDocument = null;
    this.directiveDocumentStatus = 'idle';
    this.directiveDocumentError = '';
    this.directiveDocumentTab = 'document';
    this.directivePdfStatus = 'idle';
    this.directivePdfError = '';
    this.directiveDocumentTrigger = undefined;

    if (!restoreFocus) return;
    void this.updateComplete.then(() => {
      const fallback =
        this.renderRoot.querySelector<HTMLElement>('.thread-toggle');
      if (trigger?.isConnected && trigger.getClientRects().length > 0) {
        trigger.focus();
      } else {
        fallback?.focus();
      }
    });
  }

  private abortDirectiveDocumentRequests(): void {
    this.directiveDocumentAbort?.abort();
    this.directivePdfAbort?.abort();
    this.directiveDocumentAbort = undefined;
    this.directivePdfAbort = undefined;
  }

  private revokeDirectivePdfUrl(): void {
    if (this.directivePdfObjectUrl) {
      URL.revokeObjectURL(this.directivePdfObjectUrl);
      this.directivePdfObjectUrl = undefined;
    }
    this.directivePdfUrl = null;
  }

  // ---------------------------------------------------------------- history
  private async openConversation(id: string): Promise<void> {
    const identityGeneration = this.identityGeneration;
    const chatGeneration = this.cancelActiveChat();
    this.selectedMemory = null;
    try {
      const doc = await this.client.getConversation(id);
      if (
        identityGeneration !== this.identityGeneration ||
        chatGeneration !== this.chatGeneration
      ) return;
      this.conversationId = doc.id;
      this.turns = (doc.messages ?? []).map((m) => ({
        id: this.nextTurnId(),
        role: m.role === 'user' ? 'user' : 'assistant',
        text: m.content,
        surfaces: [],
        createdAt: m.created_at,
        usage: m.usage,
        tools: [...new Set(m.tools ?? [])],
        citations: m.citations ?? [],
      }));
      if (doc.metadata?.agent_type) this.agentType = doc.metadata.agent_type;
      if (!THREAD_RAIL_DESKTOP.matches) this.sidebarOpen = false;
    } catch (e) {
      uiLogger.error('openConversation failed', e);
      this.showToast('Could not open thread');
    }
  }

  private async renameConversation(c: ConversationSummary): Promise<void> {
    const title = window.prompt('Rename thread', c.title ?? '')?.trim();
    if (!title) return;
    const generation = this.identityGeneration;
    try {
      await this.client.renameConversation(c.id, title);
      if (generation === this.identityGeneration) await this.refreshConversations(generation);
    } catch {
      if (generation === this.identityGeneration) this.showToast('Thread was not renamed');
    }
  }

  private async deleteConversation(c: ConversationSummary): Promise<void> {
    if (!window.confirm(`Delete "${c.title ?? c.id}"? Its saved memory will also be removed.`)) return;
    const generation = this.identityGeneration;
    try {
      await this.client.deleteConversation(c.id);
      if (generation !== this.identityGeneration) return;
      if (this.conversationId === c.id) this.newChat();
      await this.refreshConversations(generation);
      await this.refreshMemories(generation);
    } catch {
      if (generation === this.identityGeneration) this.showToast('Thread was not deleted');
    }
  }

  private async memorise(c: ConversationSummary): Promise<void> {
    const generation = this.identityGeneration;
    this.memorisedIds = new Set(this.memorisedIds).add(c.id);
    try {
      await this.client.createMemory(c.id, c.title);
      if (generation !== this.identityGeneration) return;
      await this.refreshMemories(generation);
      this.showToast('Thread saved to memory');
    } catch {
      if (generation !== this.identityGeneration) return;
      const next = new Set(this.memorisedIds);
      next.delete(c.id);
      this.memorisedIds = next;
      this.showToast('Thread was not saved to memory');
    }
  }

  // ---------------------------------------------------------------- memory
  private runMemorySearch = async (): Promise<void> => {
    const generation = this.identityGeneration;
    const q = this.memoryQuery.trim();
    if (!q) {
      this.searchResults = null;
      return;
    }
    try {
      const results = await this.client.searchMemories(q);
      if (generation === this.identityGeneration) this.searchResults = results;
    } catch {
      if (generation === this.identityGeneration) this.showToast('Memory search failed. Try again.');
    }
  };

  private clearMemorySearch = (): void => {
    this.memoryQuery = '';
    this.searchResults = null;
  };

  private async deleteMemory(m: MemoryRow): Promise<void> {
    const generation = this.identityGeneration;
    try {
      await this.client.deleteMemory(m.id);
      if (generation !== this.identityGeneration) return;
      if (this.selectedMemory?.id === m.id) this.selectedMemory = null;
      await this.refreshMemories(generation);
      await this.runMemorySearch();
      this.showToast('Memory deleted');
    } catch {
      if (generation === this.identityGeneration) this.showToast('Memory was not deleted');
    }
  }

  // ---------------------------------------------------------------- profile
  private openProfile = async (trigger?: HTMLElement): Promise<void> => {
    if (this.directiveDocumentReference) return;
    const identityGeneration = this.identityGeneration;
    const loadGeneration = ++this.profileLoadGeneration;
    const activeElement = this.shadowRoot?.activeElement;
    this.profileTrigger =
      trigger ?? (activeElement instanceof HTMLElement ? activeElement : undefined);
    try {
      const profile = await this.client.getProfile();
      if (
        identityGeneration !== this.identityGeneration
        || loadGeneration !== this.profileLoadGeneration
        || this.directiveDocumentReference
      ) return;
      this.profile = profile;
      this.profileDraft = JSON.stringify(this.profileToSections(profile), null, 2);
      this.profileOpen = true;
      if (!THREAD_RAIL_DESKTOP.matches) this.sidebarOpen = false;
    } catch {
      if (
        identityGeneration === this.identityGeneration
        && loadGeneration === this.profileLoadGeneration
        && !this.directiveDocumentReference
      ) {
        this.showToast('Could not load profile');
      }
    }
  };

  private closeProfile(): void {
    this.profileOpen = false;
    void this.updateComplete.then(() => {
      const trigger = this.profileTrigger;
      const fallback = this.renderRoot.querySelector<HTMLElement>('.thread-toggle');
      const triggerStyle = trigger ? getComputedStyle(trigger) : undefined;
      const threadRail = trigger?.closest('.thread-rail');
      const memoryRail = trigger?.closest('.memory-rail');
      const containingRailIsAvailable =
        (!threadRail || !threadRail.classList.contains('collapsed') || THREAD_RAIL_DESKTOP.matches)
        && (!memoryRail || !memoryRail.classList.contains('collapsed'));
      const triggerIsAvailable = trigger?.isConnected
        && containingRailIsAvailable
        && triggerStyle?.display !== 'none'
        && triggerStyle?.visibility === 'visible'
        && trigger.getClientRects().length > 0;
      if (trigger && triggerIsAvailable) trigger.focus();
      else fallback?.focus();
    });
  }

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
    const generation = this.identityGeneration;
    let sections: Record<string, unknown>;
    try {
      sections = JSON.parse(this.profileDraft);
    } catch {
      this.showToast('Profile JSON is invalid');
      return;
    }
    try {
      const profile = await this.client.putProfile(sections);
      if (generation !== this.identityGeneration) return;
      this.profile = profile;
      this.profileDraft = JSON.stringify(this.profileToSections(this.profile), null, 2);
      this.showToast('Profile saved');
    } catch {
      if (generation === this.identityGeneration) this.showToast('Profile was not saved');
    }
  };

  private generateProfile = async (): Promise<void> => {
    if (!this.conversationId) {
      this.showToast('Open or start a thread first');
      return;
    }
    const generation = this.identityGeneration;
    const conversationId = this.conversationId;
    try {
      const res = await this.client.generateProfile(conversationId);
      if (generation !== this.identityGeneration) return;
      if (res.updated && res.profile) {
        this.profile = res.profile;
        this.profileDraft = JSON.stringify(this.profileToSections(res.profile), null, 2);
        this.showToast('Profile generated from thread');
      } else {
        this.showToast('No new profile facts found');
      }
    } catch {
      if (generation === this.identityGeneration) this.showToast('Profile was not generated');
    }
  };

  private clearProfile = async (): Promise<void> => {
    if (!window.confirm('Delete the entire profile for this user?')) return;
    const generation = this.identityGeneration;
    try {
      await this.client.deleteProfile();
      if (generation !== this.identityGeneration) return;
      const profile = await this.client.getProfile();
      if (generation !== this.identityGeneration) return;
      this.profile = profile;
      this.profileDraft = JSON.stringify(this.profileToSections(this.profile), null, 2);
      this.showToast('Profile cleared');
    } catch {
      if (generation === this.identityGeneration) this.showToast('Profile was not cleared');
    }
  };

  // ---------------------------------------------------------------- chat
  private send = async (): Promise<void> => {
    const text = this.input.trim();
    const agentAvailable = this.agentOptions.some(
      (agent) => agent.available && agent.agent_type === this.agentType,
    );
    if (!text || this.busy || !agentAvailable) return;
    this.input = '';
    this.busy = true;
    this.selectedMemory = null;

    const createdAt = new Date().toISOString();
    const userTurn: ChatTurn = {
      id: this.nextTurnId(),
      role: 'user',
      text,
      surfaces: [],
      createdAt,
      tools: [],
      citations: [],
    };
    let assistantTurn: ChatTurn = {
      id: this.nextTurnId(),
      role: 'assistant',
      text: '',
      surfaces: [],
      createdAt,
      tools: [],
      citations: [],
    };
    let eventState = createChatEventState(assistantTurn, this.surfaceSeq);
    this.turns = [...this.turns, userTurn, assistantTurn];

    const isNew = this.conversationId === null;
    const identityGeneration = this.identityGeneration;
    const chatGeneration = ++this.chatGeneration;
    const controller = new AbortController();
    this.chatAbort = controller;

    try {
      await this.client.chat(text, this.conversationId, this.agentType, {
        onConversationId: (id) => {
          if (
            identityGeneration === this.identityGeneration &&
            chatGeneration === this.chatGeneration
          ) this.conversationId = id;
        },
        onEvent: (ev) => {
          if (
            identityGeneration === this.identityGeneration &&
            chatGeneration === this.chatGeneration
          ) {
            eventState = reduceChatEvent(eventState, ev);
            assistantTurn = eventState.turn;
            this.surfaceSeq = eventState.nextSurfaceSequence;
            this.replaceTurn(assistantTurn);
          }
        },
      }, controller.signal);
    } catch (e) {
      if (
        identityGeneration === this.identityGeneration &&
        chatGeneration === this.chatGeneration &&
        !controller.signal.aborted
      ) {
        assistantTurn = { ...assistantTurn, error: String(e) };
      }
    } finally {
      if (this.chatAbort === controller) this.chatAbort = undefined;
      if (
        identityGeneration !== this.identityGeneration ||
        chatGeneration !== this.chatGeneration
      ) return;
      this.busy = false;
      assistantTurn = {
        ...assistantTurn,
        createdAt: new Date().toISOString(),
      };
      this.replaceTurn(assistantTurn);
      // A turn was persisted server-side; refresh history (new convo appears / updates).
      void this.refreshConversations();
      if (isNew) void this.refreshMe();
    }
  };

  private replaceTurn(updated: ChatTurn): void {
    this.turns = this.turns.map((turn) =>
      turn.id === updated.id ? updated : turn,
    );
  }
  // ---------------------------------------------------------------- render
  render() {
    if (getConfig().authMode === 'entra' && this.authStatus !== 'signed-in') {
      return this.renderAuthentication();
    }

    const name = this.currentUserName;
    const email = this.currentUserEmail;
    const initials = (this.me?.initials as string)
      ?? name
        .split(/\s+/)
        .map((part) => part[0])
        .join('')
        .slice(0, 2)
        .toUpperCase();
    const activeConversation = this.conversationId
      ? this.conversations.find((conversation) => conversation.id === this.conversationId)
      : undefined;
    const threadKind = this.agentType === 'directive-rag'
      ? 'directive thread'
      : 'support thread';
    const title = activeConversation?.title
      ?? (this.conversationId ? `Active ${threadKind}` : `New ${threadKind}`);
    const memoryRailOpen = (
      this.memoryPanelOpen && this.rightRailMode === 'memory'
    );
    const sourceRailOpen = (
      this.memoryPanelOpen && this.rightRailMode === 'sources'
    );

    return html`
      <div class="app-shell" @keydown=${this.onAppKeydown}>
        <header class="app-header">
          <button
            class="icon-button thread-toggle"
            type="button"
            aria-label=${this.sidebarOpen ? 'Hide thread history' : 'Show thread history'}
            aria-controls="thread-panel"
            aria-expanded=${this.sidebarOpen}
            @click=${this.toggleSidebar}
          >
            <span class="material-symbols-outlined">menu</span>
          </button>

          <div class="brand" aria-label="Memory Thread">
            <span class="brand-mark" aria-hidden="true">
              <span></span><span></span><span></span>
            </span>
            <span class="brand-name">Memory Thread</span>
          </div>

          <div class="header-actions">
            <a
              class="header-button"
              href="${REPOSITORY_URL}/tree/main/docs"
              target="_blank"
              rel="noreferrer"
              aria-label="Documentation"
              title="Documentation"
            >
              <span class="material-symbols-outlined">description</span>
            </a>
            <a
              class="header-button"
              href="${REPOSITORY_URL}#current-architecture"
              target="_blank"
              rel="noreferrer"
              aria-label="Architecture"
              title="Architecture"
            >
              <span class="material-symbols-outlined">account_tree</span>
            </a>
            <a
              class="header-button"
              href=${REPOSITORY_URL}
              target="_blank"
              rel="noreferrer"
              aria-label="GitHub source"
              title="GitHub source"
            >
              <svg class="github-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.69c-2.78.61-3.37-1.19-3.37-1.19-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.04 1.53 1.04.9 1.53 2.35 1.09 2.92.83.09-.65.35-1.09.64-1.34-2.22-.25-4.56-1.11-4.56-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.58 9.58 0 0 1 12 7.01c.85 0 1.7.11 2.5.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.86v2.58c0 .27.18.58.69.48A10 10 0 0 0 12 2Z"
              ></path>
              </svg>
            </a>
            <button
              class="header-button"
              type="button"
              aria-label=${this.theme === 'light' ? 'Use dark theme' : 'Use light theme'}
              title=${this.theme === 'light' ? 'Dark theme' : 'Light theme'}
              @click=${this.toggleTheme}
            >
              <span class="material-symbols-outlined">
              ${this.theme === 'light' ? 'dark_mode' : 'light_mode'}
              </span>
            </button>
            <span
              class="header-separator"
              role="separator"
              aria-orientation="vertical"
            ></span>
            <button
              class="header-button"
              type="button"
              aria-label=${memoryRailOpen ? 'Hide saved memory' : 'Show saved memory'}
              aria-controls="memory-panel"
              aria-expanded=${memoryRailOpen}
              title="Saved memory"
              @click=${this.toggleMemoryPanel}
            >
              <span class="material-symbols-outlined">database</span>
            </button>
            ${this.canManageDirectiveSources
              ? html`<button
                  class="header-button"
                  type="button"
                  aria-label=${sourceRailOpen
                    ? 'Hide source documents'
                    : 'Show source documents'}
                  aria-controls="source-documents-panel"
                  aria-expanded=${sourceRailOpen}
                  title="Source documents"
                  @click=${this.toggleSourceDocumentsPanel}
                >
                  <span class="material-symbols-outlined">upload_file</span>
                </button>`
              : nothing}
          </div>
        </header>

        <div class="body">
          <conversation-sidebar
            .open=${this.sidebarOpen}
            .conversations=${this.conversations}
            .status=${this.conversationsStatus}
            .memorisedIds=${this.memorisedIds}
            .activeConversationId=${this.conversationId}
            .initials=${initials}
            .userName=${name}
            .userEmail=${email}
            .mockUser=${this.mockUser}
            .actions=${this.sidebarActions}
          ></conversation-sidebar>

          <section class="chat-col" aria-labelledby="conversation-title">
            <header class="conversation-header">
              <h1 class="conversation-title" id="conversation-title">${title}</h1>
            </header>

          <main
            class="conversation-main"
            aria-label="Conversation"
            aria-live="polite"
            aria-relevant="additions text"
          >
            <chat-transcript
              .turns=${this.turns}
              .busy=${this.busy}
              .agentType=${this.agentType}
              .agentOptions=${this.agentOptions}
              .userName=${name}
              .actions=${this.transcriptActions}
            ></chat-transcript>
          </main>
          <chat-composer
            .input=${this.input}
            .busy=${this.busy}
            .conversationActive=${this.conversationId !== null}
            .agentType=${this.agentType}
            .agentOptions=${this.agentOptions}
            .actions=${this.composerActions}
          ></chat-composer>
          </section>

          <memory-rail
            .open=${memoryRailOpen}
            .memories=${this.memories}
            .searchResults=${this.searchResults}
            .selectedMemory=${this.selectedMemory}
            .status=${this.memoriesStatus}
            .query=${this.memoryQuery}
            .actions=${this.memoryActions}
          ></memory-rail>
          ${this.canManageDirectiveSources
            ? html`<source-documents-rail
                .open=${sourceRailOpen}
                .canManage=${this.canManageDirectiveSources}
                .documents=${this.directiveSources}
                .status=${this.directiveSourcesStatus}
                .uploadStatus=${this.directiveSourceUploadStatus}
                .uploadProgress=${this.directiveSourceUploadProgress}
                .uploadError=${this.directiveSourceUploadError}
                .deletingFilename=${this.directiveSourceDeletingFilename}
                .deleting=${this.directiveSourceDeleting}
                .deleteError=${this.directiveSourceDeleteError}
                .nextCursor=${this.directiveSourcesNextCursor}
                .loadingMore=${this.directiveSourceListLoading}
                .actions=${this.sourceDocumentsActions}
              ></source-documents-rail>`
            : nothing}
        </div>

        ${this.sidebarOpen
          ? html`<button
            class="panel-scrim thread-scrim"
            type="button"
            aria-label="Close thread history"
            @click=${() => (this.sidebarOpen = false)}
          ></button>`
          : nothing}
        ${this.memoryPanelOpen
          ? html`<button
            class="panel-scrim memory-scrim"
            type="button"
            aria-label=${this.rightRailMode === 'sources'
              ? 'Close source documents'
              : 'Close saved memory'}
            @click=${() => (this.memoryPanelOpen = false)}
          ></button>`
          : nothing}

        <profile-drawer
          .open=${this.profileOpen}
          .profile=${this.profile}
          .draft=${this.profileDraft}
          .actions=${this.profileActions}
        ></profile-drawer>
        <directive-document-viewer
          .open=${this.directiveDocumentReference !== null}
          .reference=${this.directiveDocumentReference}
          .document=${this.directiveDocument}
          .documentStatus=${this.directiveDocumentStatus}
          .documentError=${this.directiveDocumentError}
          .activeTab=${this.directiveDocumentTab}
          .pdfStatus=${this.directivePdfStatus}
          .pdfError=${this.directivePdfError}
          .pdfUrl=${this.directivePdfUrl}
          .actions=${this.directiveDocumentActions}
        ></directive-document-viewer>
        ${this.toast
          ? html`<div class="toast" role="status" aria-live="polite">${this.toast}</div>`
          : nothing}
      </div>
    `;
  }

  private renderAuthentication() {
    const checking = this.authStatus === 'checking';
    const signingIn = this.authStatus === 'signing-in';
    return html`
      <main class="auth-shell" aria-labelledby="auth-title">
        <button
          class="auth-theme-toggle"
          type="button"
          aria-label=${this.theme === 'light' ? 'Use dark theme' : 'Use light theme'}
          title=${this.theme === 'light' ? 'Dark theme' : 'Light theme'}
          @click=${this.toggleTheme}
        >
          <span class="material-symbols-outlined">
            ${this.theme === 'light' ? 'dark_mode' : 'light_mode'}
          </span>
        </button>

        <section class="auth-card" aria-busy=${checking || signingIn}>
          <div class="auth-brand">
            <span class="brand-mark" aria-hidden="true">
              <span></span><span></span><span></span>
            </span>
            <span class="brand-name">Memory Thread</span>
          </div>

          <div class="auth-heading">
            <p class="auth-eyebrow">Grounded support workspace</p>
            <h1 id="auth-title">${checking ? 'Checking your session' : 'Welcome back'}</h1>
            <p>
              ${checking
                ? 'Confirming your Microsoft Entra ID session before loading the workspace.'
                : 'Sign in with your organizational account to access your conversations and memory.'}
            </p>
          </div>

          ${checking
            ? html`<div class="auth-progress" role="status">
                <span class="auth-spinner" aria-hidden="true"></span>
                <span>Connecting securely...</span>
              </div>`
            : html`
                ${this.authError
                  ? html`<div class="auth-error" role="alert">
                      <span class="material-symbols-outlined" aria-hidden="true">error</span>
                      <span>${this.authError}</span>
                    </div>`
                  : nothing}
                <button
                  class="entra-sign-in"
                  type="button"
                  ?disabled=${signingIn}
                  @click=${this.signInToApplication}
                >
                  <span class="microsoft-mark" aria-hidden="true">
                    <span></span><span></span><span></span><span></span>
                  </span>
                  <span>${signingIn ? 'Signing in...' : 'Sign in with Microsoft Entra ID'}</span>
                </button>
              `}

          <p class="auth-footnote">
            Access is restricted to authorized organizational accounts.
          </p>
        </section>
      </main>
    `;
  }

  private get currentUserName(): string {
    const apiName = this.me?.display_name;
    if (typeof apiName === 'string' && apiName.trim()) return apiName;
    return this.authSession?.displayName || this.mockUser;
  }

  private get currentUserEmail(): string | null {
    const apiEmail = this.me?.email;
    if (typeof apiEmail === 'string' && apiEmail.trim()) return apiEmail;
    return this.authSession?.username || null;
  }

  private get canManageDirectiveSources(): boolean {
    return this.me?.can_manage_directive_sources === true;
  }

}

// Touch getConfig so tree-shaking keeps runtime config wiring available.
