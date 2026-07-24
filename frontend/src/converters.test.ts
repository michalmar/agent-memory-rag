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
