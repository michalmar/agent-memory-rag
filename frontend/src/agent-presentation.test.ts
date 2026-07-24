import { describe, expect, it } from 'vitest';

import {
  agentDescription,
  agentIcon,
  agentLabel,
} from './agent-presentation.js';

describe('agent presentation', () => {
  it('provides exhaustive directive metadata without changing support labels', () => {
    expect(agentLabel([], 'foundry-prompt')).toBe('Foundry Prompt Agent');
    expect(agentLabel([], 'agent-framework')).toBe('Hosted Agent Framework');
    expect(agentLabel([], 'directive-rag')).toBe('Directive Assistant');
    expect(agentDescription('directive-rag')).toContain('summaries');
    expect(agentIcon('directive-rag')).toBe('policy');
  });
});
