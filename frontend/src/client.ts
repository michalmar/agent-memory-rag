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

export class AGUIClient {
  private base = getConfig().apiBaseUrl;

  async me(): Promise<Record<string, unknown>> {
    const res = await fetch(`${this.base}/me`, { headers: await getAuthHeaders() });
    if (!res.ok) throw new Error(`/me ${res.status}`);
    return res.json();
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
