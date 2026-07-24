import { A2UIProcessor } from './a2ui/processor.js';
import type { ChatTurn } from './chat-models.js';
import type {
  AGUIEvent,
  TokenUsage,
  WorkflowProgress,
  WorkflowStage,
  WorkflowStatus,
} from './client.js';
import {
  extractToolCitations,
  mergeCitations,
  parseCitations,
} from './citations.js';
import { convertToolResult } from './converters.js';

export interface ChatEventState {
  turn: ChatTurn;
  toolNames: ReadonlyMap<string, string>;
  nextSurfaceSequence: number;
}

const PROGRESS_MESSAGES: Record<WorkflowStage, string> = {
  resolving: 'Resolving directive scope',
  searching: 'Searching published directives',
  loading_content: 'Loading directive content',
  following_references: 'Following directive references',
  comparing_versions: 'Comparing directive versions',
  checking_mandatory_status: 'Checking mandatory status',
  verifying_coverage: 'Verifying source coverage',
  preparing_answer: 'Preparing answer',
};
const WORKFLOW_STATUSES = new Set<WorkflowStatus>([
  'started',
  'in_progress',
  'completed',
  'failed',
  'cancelled',
]);

export function createChatEventState(
  turn: ChatTurn,
  nextSurfaceSequence: number,
): ChatEventState {
  return {
    turn,
    toolNames: new Map(),
    nextSurfaceSequence,
  };
}

export function readUsage(value: unknown): TokenUsage | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const usage = value as Record<string, unknown>;
  const result: TokenUsage = {};
  for (const field of [
    'input_tokens',
    'output_tokens',
    'cached_tokens',
  ] as const) {
    const tokenCount = usage[field];
    if (
      typeof tokenCount === 'number' &&
      Number.isFinite(tokenCount) &&
      tokenCount >= 0
    ) {
      result[field] = tokenCount;
    }
  }
  return Object.keys(result).length ? result : undefined;
}

export function readProgress(
  value: unknown,
): WorkflowProgress | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  const progress = value as Record<string, unknown>;
  const stage = progress.stage as WorkflowStage;
  const status = progress.status as WorkflowStatus;
  if (!(stage in PROGRESS_MESSAGES) || !WORKFLOW_STATUSES.has(status)) {
    return undefined;
  }
  const result: WorkflowProgress = {
    stage,
    status,
    message: PROGRESS_MESSAGES[stage],
  };
  for (const field of ['completed_count', 'total_count'] as const) {
    const count = progress[field];
    if (
      typeof count === 'number'
      && Number.isInteger(count)
      && count >= 0
    ) {
      result[field] = count;
    }
  }
  if (
    result.completed_count != null
    && result.total_count != null
    && result.completed_count > result.total_count
  ) {
    delete result.completed_count;
    delete result.total_count;
  }
  if (progress.heartbeat === true) result.heartbeat = true;
  return result;
}

function buildToolSurface(
  toolName: string,
  content: string,
  surfaceId: string,
) {
  const messages = convertToolResult(toolName, content, surfaceId);
  if (messages.length === 0) return undefined;

  const processor = new A2UIProcessor();
  for (const message of messages) processor.apply(message);
  return processor.getSurface(surfaceId);
}

export function reduceChatEvent(
  state: ChatEventState,
  event: AGUIEvent,
): ChatEventState {
  const turn: ChatTurn = {
    ...state.turn,
    surfaces: [...state.turn.surfaces],
    tools: [...state.turn.tools],
    citations: state.turn.citations.map((citation) => ({ ...citation })),
  };
  const toolNames = new Map(state.toolNames);
  let nextSurfaceSequence = state.nextSurfaceSequence;

  switch (event.type) {
    case 'TEXT_MESSAGE_CONTENT':
      turn.text += event.delta ?? '';
      break;
    case 'TOOL_CALL_START':
      if (event.toolCallId && event.toolCallName) {
        toolNames.set(event.toolCallId, event.toolCallName);
        if (!turn.tools.includes(event.toolCallName)) {
          turn.tools.push(event.toolCallName);
        }
      }
      break;
    case 'TOOL_CALL_RESULT': {
      const toolName = event.toolCallId
        ? toolNames.get(event.toolCallId)
        : undefined;
      if (event.content) {
        turn.citations = mergeCitations(
          turn.citations,
          extractToolCitations(event.content),
        );
        if (toolName) {
          const surfaceId = `s-${nextSurfaceSequence}`;
          nextSurfaceSequence += 1;
          const surface = buildToolSurface(
            toolName,
            event.content,
            surfaceId,
          );
          if (surface) turn.surfaces.push(surface);
        }
      }
      break;
    }
    case 'TOOL_CALL_END':
      if (event.toolCallId) toolNames.delete(event.toolCallId);
      break;
    case 'CUSTOM':
      if (event.name === 'agent_usage') {
        const usage = readUsage(event.value);
        if (usage) turn.usage = usage;
      } else if (event.name === 'agent_citations') {
        turn.citations = mergeCitations(
          turn.citations,
          parseCitations(event.value),
        );
      } else if (event.name === 'agent_progress') {
        const progress = readProgress(event.value);
        if (progress) turn.progress = progress;
      }
      break;
    case 'RUN_FINISHED':
      if (
        turn.progress
        && !['completed', 'failed', 'cancelled'].includes(
          turn.progress.status,
        )
      ) {
        turn.progress = {
          ...turn.progress,
          status: 'completed',
          message: 'Answer ready',
          heartbeat: false,
        };
      }
      break;
    case 'RUN_ERROR':
      turn.error = event.message ?? 'Run error';
      if (turn.progress) {
        turn.progress = {
          ...turn.progress,
          status: 'failed',
          message: 'Directive request failed',
          heartbeat: false,
        };
      }
      break;
    default:
      break;
  }

  return { turn, toolNames, nextSurfaceSequence };
}
