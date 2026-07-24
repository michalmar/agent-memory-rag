// client.ts — REST + AG-UI SSE client (§B3/B4 contract).
import { getAuthHeaders, getConfig } from './auth.js';
import { uiLogger } from './ui-logger.js';

export interface AGUIEvent {
  type: string;
  threadId?: string;
  runId?: string;
  messageId?: string;
  delta?: string;
  toolCallId?: string;
  toolCallName?: string;
  content?: string;
  message?: string;
  code?: string;
  name?: string;
  value?: unknown;
}

export interface ChatHandlers {
  onEvent: (ev: AGUIEvent) => void;
  onConversationId?: (id: string) => void;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
  usage?: TokenUsage;
  tools?: string[];
  citations?: CitationSource[];
}

export type AgentType =
  | 'foundry-prompt'
  | 'agent-framework'
  | 'directive-rag';

export type MandatoryStatus =
  | 'mandatory'
  | 'non_mandatory'
  | 'unknown';

export type WorkflowStage =
  | 'resolving'
  | 'searching'
  | 'loading_content'
  | 'following_references'
  | 'comparing_versions'
  | 'checking_mandatory_status'
  | 'verifying_coverage'
  | 'preparing_answer';

export type WorkflowStatus =
  | 'started'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface WorkflowProgress {
  stage: WorkflowStage;
  status: WorkflowStatus;
  message: string;
  completed_count?: number;
  total_count?: number;
  heartbeat?: boolean;
}

export interface TokenUsage {
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
}

export interface CitationSource {
  ref_id: string;
  source_name: string;
  search_idx?: number | null;
  url?: string | null;
  directive_id?: string;
  directive_version_id?: string;
  version_label?: string;
  section_id?: string;
  section_number?: string;
  section_title?: string;
  page_from?: number;
  page_to?: number;
  effective_from?: string;
  mandatory_status?: MandatoryStatus;
  mandate_snapshot_id?: string;
  retrieval_strategy?: string;
  coverage?: Record<string, unknown>;
}

export interface RuntimeMetadata {
  agent_type?: AgentType;
  agent_label?: string;
  release_label?: string;
  agent_version?: string;
}

export interface ConversationSummary {
  id: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
  metadata?: RuntimeMetadata;
}

export interface ConversationDoc extends ConversationSummary {
  messages: ChatMessage[];
}

export interface AgentOption {
  agent_type: AgentType;
  label: string;
  available: boolean;
}

export interface AgentCapabilities {
  retrieval: 'Foundry IQ';
  agents: AgentOption[];
}

export interface MemoryRow {
  id: string;
  conversation_id: string;
  summary: string;
  source_title?: string;
  message_count?: number;
  created_at?: string;
  updated_at?: string;
  similarity?: number;
}

export interface ProfileDoc {
  version: number;
  basic_info?: Record<string, unknown>;
  interests?: string[];
  habits?: string[];
  preferences?: Record<string, unknown>;
  status?: Record<string, unknown>;
  facts?: string[];
  updated_at?: string;
}

export class AGUIClient {
  private base = getConfig().apiBaseUrl;

  async me(): Promise<Record<string, unknown>> {
    return this.req('/me');
  }

  // ------------------------------------------------------------------ REST helpers
  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = {
      ...(await getAuthHeaders()),
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init.headers ?? {}),
    };
    const res = await fetch(`${this.base}${path}`, { ...init, headers });
    if (!res.ok) throw new Error(`${path} ${res.status}`);
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  // Conversation history (Cosmos)
  listConversations(): Promise<ConversationSummary[]> {
    return this.req('/conversations');
  }
  getConversation(id: string): Promise<ConversationDoc> {
    return this.req(`/conversations/${encodeURIComponent(id)}`);
  }
  renameConversation(id: string, title: string): Promise<unknown> {
    return this.req(`/conversations/${encodeURIComponent(id)}/title`, {
      method: 'PUT',
      body: JSON.stringify({ title }),
    });
  }
  deleteConversation(id: string): Promise<unknown> {
    return this.req(`/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  getAgentCapabilities(): Promise<AgentCapabilities> {
    return this.req('/agents');
  }

  // Conversation memory (Cosmos vector search)
  listMemories(): Promise<MemoryRow[]> {
    return this.req('/memories');
  }
  createMemory(conversationId: string, title?: string): Promise<MemoryRow> {
    return this.req('/memories', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, title }),
    });
  }
  searchMemories(query: string, limit = 5): Promise<MemoryRow[]> {
    return this.req('/memories/search', {
      method: 'POST',
      body: JSON.stringify({ query, limit }),
    });
  }
  deleteMemory(id: string): Promise<unknown> {
    return this.req(`/memories/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  // User profile (Cosmos)
  getProfile(): Promise<ProfileDoc> {
    return this.req('/profile');
  }
  putProfile(sections: Record<string, unknown>): Promise<ProfileDoc> {
    return this.req('/profile', { method: 'PUT', body: JSON.stringify({ sections }) });
  }
  generateProfile(conversationId: string): Promise<{ updated: boolean; profile?: ProfileDoc }> {
    return this.req('/profile/generate', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId }),
    });
  }
  deleteProfile(): Promise<unknown> {
    return this.req('/profile', { method: 'DELETE' });
  }

  /** POST /chat and stream AG-UI events to the handler. */
  async chat(
    message: string,
    conversationId: string | null,
    agentType: AgentType,
    handlers: ChatHandlers,
    signal?: AbortSignal,
  ): Promise<void> {
    const headers = {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    };
    const res = await fetch(`${this.base}/chat`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
        agent_type: agentType,
      }),
      signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`/chat ${res.status}`);
    }
    const id = res.headers.get('X-Conversation-ID');
    if (id && handlers.onConversationId) handlers.onConversationId(id);

    const reader = res.body.getReader();
    const cancelReader = () => {
      void reader.cancel();
    };
    signal?.addEventListener('abort', cancelReader, { once: true });
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          this.dispatchFrame(frame, handlers);
        }
      }
      if (buffer.trim()) this.dispatchFrame(buffer, handlers);
    } finally {
      signal?.removeEventListener('abort', cancelReader);
      reader.releaseLock();
    }
  }

  private dispatchFrame(frame: string, handlers: ChatHandlers): void {
    for (const line of frame.split('\n')) {
      const trimmed = line.trimStart();
      if (!trimmed.startsWith('data:')) continue;
      const payload = trimmed.slice(5).trim();
      if (!payload) continue;
      try {
        handlers.onEvent(JSON.parse(payload) as AGUIEvent);
      } catch (e) {
        uiLogger.error('bad SSE frame', payload, e);
      }
    }
  }
}
