import type { SurfaceState } from './a2ui/processor.js';
import type { CitationSource, TokenUsage } from './client.js';

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
}

export type ResourceStatus = 'loading' | 'ready' | 'error';
