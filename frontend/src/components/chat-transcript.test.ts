import { describe, expect, it } from 'vitest';

import {
  emptyStatePrompt,
  toolActivityLabel,
} from './chat-transcript.js';

describe('chat transcript presentation', () => {
  it('uses current-only directive tool labels', () => {
    expect(toolActivityLabel('get_directive')).toBe('Directive details');
    expect(toolActivityLabel('search_directives')).toBe('Directive search');
    expect(toolActivityLabel('get_directive_content')).toBe('Directive content');
    expect(toolActivityLabel('get_user_directive_mandates')).toBe('Mandatory status');
  });

  it('removes relation and history wording from the directive welcome copy', () => {
    expect(emptyStatePrompt('directive-rag')).toBe(
      'Search current directives, review exact content, or check mandatory status.',
    );
    expect(emptyStatePrompt('agent-framework')).toBe(
      'Ask about an order, a policy, or saved context.',
    );
  });
});
