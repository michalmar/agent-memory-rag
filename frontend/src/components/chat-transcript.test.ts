import { describe, expect, it } from 'vitest';

import {
  emptyStatePrompt,
  formatRelativeTime,
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

  it('formats response ages without individual seconds', () => {
    const now = Date.parse('2026-08-21T13:48:30.000Z');

    expect(formatRelativeTime('2026-08-21T13:48:01.000Z', now)).toBe(
      'moment ago',
    );
    expect(formatRelativeTime('2026-08-21T13:48:00.000Z', now)).toBe(
      '1 min ago',
    );
    expect(formatRelativeTime('2026-08-21T13:47:30.000Z', now)).toBe(
      '1 min ago',
    );
    expect(formatRelativeTime('2026-08-21T12:46:30.000Z', now)).toBe(
      '62 min ago',
    );
  });
});
