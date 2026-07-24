import type { SurfaceState } from './a2ui/processor.js';
import type {
  CitationSource,
  TokenUsage,
  WorkflowProgress,
} from './client.js';

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  surfaces: SurfaceState[];
  createdAt?: string;
  usage?: TokenUsage;
  tools: string[];
  citations: CitationSource[];
  feedback?: 'up' | 'down';
  error?: string;
  progress?: WorkflowProgress;
}

export type ResourceStatus = 'loading' | 'ready' | 'error';
