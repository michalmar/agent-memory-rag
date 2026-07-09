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
}

export interface ChatHandlers {
  onEvent: (ev: AGUIEvent) => void;
  onSessionId?: (id: string) => void;
}

export interface ChatMessage {
  role: string;
  content: string;
}

export interface ConversationSummary {
  id: string;
  user_id: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
}

export interface ConversationDoc extends ConversationSummary {
  messages: ChatMessage[];
  metadata?: Record<string, unknown>;
}

export interface MemoryRow {
  id: string;
  conversation_id: string;
  user_id: string;
  summary: string;
  source_title?: string;
  message_count?: number;
  created_at?: string;
  updated_at?: string;
  similarity?: number;
}

export interface ProfileDoc {
  user_id: string;
  version: number;
  basic_info?: Record<string, unknown>;
  interests?: string[];
  habits?: string[];
  preferences?: Record<string, unknown>;
  status?: Record<string, unknown>;
  facts?: string[];
  source_conversations?: unknown[];
  updated_at?: string;
}

export class AGUIClient {
  private base = getConfig().apiBaseUrl;

  async me(): Promise<Record<string, unknown>> {
    const res = await fetch(`${this.base}/me`, { headers: await getAuthHeaders() });
    if (!res.ok) throw new Error(`/me ${res.status}`);
    return res.json();
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

  // Conversation memory (pgvector)
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
  generateProfile(sessionId: string): Promise<{ updated: boolean; profile?: ProfileDoc }> {
    return this.req('/profile/generate', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    });
  }
  deleteProfile(): Promise<unknown> {
    return this.req('/profile', { method: 'DELETE' });
  }

  /** POST /chat and stream AG-UI events to the handler. */
  async chat(
    messages: ChatMessage[],
    threadId: string | null,
    ragMode: string,
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
      body: JSON.stringify({ messages, thread_id: threadId, rag_mode: ragMode }),
      signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`/chat ${res.status}`);
    }
    const sid = res.headers.get('X-Session-ID');
    if (sid && handlers.onSessionId) handlers.onSessionId(sid);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
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
