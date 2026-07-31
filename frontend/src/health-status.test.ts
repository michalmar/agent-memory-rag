import { describe, expect, it } from 'vitest';

import { summarizeHealth } from './health-status.js';

describe('application health summary', () => {
  it('reports healthy when required dependencies are ready', () => {
    expect(summarizeHealth({
      status: 'ready',
      dependencies: {
        cosmos_history: { status: 'ok', required: true },
      },
    })).toMatchObject({ status: 'healthy', label: 'Healthy' });
  });

  it('reports required dependency failures as unhealthy', () => {
    expect(summarizeHealth({
      status: 'not_ready',
      dependencies: {
        directive_search: {
          status: 'failed',
          error: 'DirectiveDataUnavailable',
          required: true,
        },
      },
    })).toEqual({
      status: 'unhealthy',
      label: 'Unhealthy',
      detail: 'Required dependency failure: directive_search',
    });
  });

  it('reports optional dependency failures as degraded', () => {
    expect(summarizeHealth({
      status: 'ready',
      dependencies: {
        cosmos_memory: { status: 'failed', required: false },
      },
      degraded_dependencies: ['cosmos_memory'],
    })).toEqual({
      status: 'degraded',
      label: 'Degraded',
      detail: 'Optional dependency failure: cosmos_memory',
    });
  });
});
