import { describe, expect, it } from 'vitest';

import {
  agentDescription,
  agentIcon,
  agentIndicator,
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

  it('builds indicators for persisted and legacy conversations', () => {
    expect(agentIndicator({
      agent_type: 'directive-rag',
      agent_label: 'Directive specialist',
    })).toEqual({
      label: 'Directive specialist',
      icon: 'policy',
    });
    expect(agentIndicator({ agent_type: 'agent-framework' })).toEqual({
      label: 'Hosted Agent Framework',
      icon: 'hub',
    });
    expect(agentIndicator()).toEqual({
      label: 'Unknown agent',
      icon: 'smart_toy',
    });
  });
});
