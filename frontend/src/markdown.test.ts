import { marked } from 'marked';
import { describe, expect, it } from 'vitest';

import { normalizeMarkdownHtmlTables } from './markdown.js';

describe('Markdown normalization', () => {
  it('ends a raw HTML table before page comments and headings', () => {
    const markdown = [
      'Before',
      '<table>',
      '<tr><td>History</td></tr>',
      '</table>',
      '<!-- PageNumber="Strana: 3" -->',
      '<!-- PageBreak -->',
      '## 1 ÚČEL',
      'After',
    ].join('\n');

    const rendered = marked.parse(normalizeMarkdownHtmlTables(markdown), {
      async: false,
    }) as string;

    expect(rendered).toContain('<table>');
    expect(rendered).toContain('<h2>1 ÚČEL</h2>');
    expect(rendered).toContain('<p>After</p>');
    expect(rendered).not.toContain('## 1 ÚČEL');
  });

  it('preserves existing blank lines around raw HTML tables', () => {
    const markdown = [
      'Before',
      '',
      '<table>',
      '<tr><td>History</td></tr>',
      '</table>',
      '',
      '## After',
    ].join('\n');

    expect(normalizeMarkdownHtmlTables(markdown)).toBe(markdown);
  });
});
