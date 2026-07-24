import { describe, expect, it } from 'vitest';

import type { ChatTurn } from './chat-models.js';
import {
  createChatEventState,
  readUsage,
  readProgress,
  reduceChatEvent,
} from './chat-event-reducer.js';

function assistantTurn(): ChatTurn {
  return {
    id: 'turn-1',
    role: 'assistant',
    text: '',
    surfaces: [],
    tools: [],
    citations: [],
  };
}

describe('chat event reducer', () => {
  it('accumulates text without mutating the previous turn', () => {
    const initial = createChatEventState(assistantTurn(), 0);
    const reduced = reduceChatEvent(initial, {
      type: 'TEXT_MESSAGE_CONTENT',
      delta: 'Hello',
    });

    expect(reduced.turn.text).toBe('Hello');
    expect(initial.turn.text).toBe('');
  });

  it('tracks tools, citations, and normalized order surfaces', () => {
    let state = createChatEventState(assistantTurn(), 7);
    state = reduceChatEvent(state, {
      type: 'TOOL_CALL_START',
      toolCallId: 'call-1',
      toolCallName: 'get_order_status',
    });
    state = reduceChatEvent(state, {
      type: 'TOOL_CALL_RESULT',
      toolCallId: 'call-1',
      content: JSON.stringify({
        data: {
          status: 'shipped',
          trackingNumber: '1Z999',
          currentStepIcon: 'local_shipping',
          eta: 'Tomorrow',
        },
        citations: [
          { ref_id: 'order', source_name: 'Order system' },
        ],
      }),
    });
    state = reduceChatEvent(state, {
      type: 'TOOL_CALL_END',
      toolCallId: 'call-1',
    });

    expect(state.turn.tools).toEqual(['get_order_status']);
    expect(state.turn.citations).toEqual([
      { ref_id: 'order', source_name: 'Order system' },
    ]);
    expect(state.turn.surfaces).toHaveLength(1);
    expect(state.turn.surfaces[0].surfaceId).toBe('s-7');
    expect(state.nextSurfaceSequence).toBe(8);
    expect(state.toolNames.size).toBe(0);
  });

  it('validates usage and preserves run errors', () => {
    expect(
      readUsage({
        input_tokens: 4,
        output_tokens: -1,
        cached_tokens: Number.NaN,
      }),
    ).toEqual({ input_tokens: 4 });

    let state = createChatEventState(assistantTurn(), 0);
    state = reduceChatEvent(state, {
      type: 'CUSTOM',
      name: 'agent_usage',
      value: { input_tokens: 4, output_tokens: 2, cached_tokens: 1 },
    });
    state = reduceChatEvent(state, {
      type: 'RUN_ERROR',
      message: 'Agent unavailable',
    });

    expect(state.turn.usage).toEqual({
      input_tokens: 4,
      output_tokens: 2,
      cached_tokens: 1,
    });
    expect(state.turn.error).toBe('Agent unavailable');
  });

  it('tracks safe directive progress, counts, and terminal state', () => {
    expect(
      readProgress({
        stage: 'loading_content',
        status: 'in_progress',
        message: 'untrusted text',
        completed_count: 2,
        total_count: 5,
        heartbeat: true,
      }),
    ).toEqual({
      stage: 'loading_content',
      status: 'in_progress',
      message: 'Loading directive content',
      completed_count: 2,
      total_count: 5,
      heartbeat: true,
    });
    expect(
      readProgress({ stage: 'hidden_reasoning', status: 'in_progress' }),
    ).toBeUndefined();

    let state = createChatEventState(assistantTurn(), 0);
    state = reduceChatEvent(state, {
      type: 'CUSTOM',
      name: 'agent_progress',
      value: {
        stage: 'searching',
        status: 'in_progress',
      },
    });
    state = reduceChatEvent(state, { type: 'RUN_FINISHED' });
    expect(state.turn.progress).toEqual({
      stage: 'searching',
      status: 'completed',
      message: 'Answer ready',
      heartbeat: false,
    });
  });
});
