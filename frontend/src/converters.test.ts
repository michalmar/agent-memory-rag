import { describe, expect, it } from 'vitest';

import { convertToolResult } from './converters.js';

describe('tool result conversion', () => {
  it('never renders raw directive or mandate payloads', () => {
    const content = JSON.stringify({
      data: {
        snapshot_id: 'snapshot-1',
        statuses: { 'DIR-1': 'mandatory' },
      },
    });
    expect(
      convertToolResult(
        'get_user_directive_mandates',
        content,
        'surface-1',
      ),
    ).toEqual([]);
    expect(
      convertToolResult('get_directive_content', content, 'surface-2'),
    ).toEqual([]);
    expect(
      convertToolResult('search_directives', content, 'surface-3'),
    ).toEqual([]);
  });

  it('suppresses citation-free get_directive responses, including not-found envelopes', () => {
    expect(
      convertToolResult(
        'get_directive',
        JSON.stringify({
          data: {
            status: 'not_found',
            directive_id: '12345678',
          },
        }),
        'surface-1',
      ),
    ).toEqual([]);
    expect(
      convertToolResult(
        'get_directive',
        JSON.stringify({
          data: {
            directive_id: '12345678',
            title: 'Directive title',
          },
        }),
        'surface-2',
      ),
    ).toEqual([]);
  });

  it('preserves existing generic support tool rendering', () => {
    expect(
      convertToolResult(
        'support_diagnostic',
        JSON.stringify({ status: 'ok' }),
        'surface-1',
      ),
    ).toHaveLength(3);
  });
});
