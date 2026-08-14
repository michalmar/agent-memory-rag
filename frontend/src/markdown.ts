import DOMPurify from 'dompurify';
import { marked } from 'marked';

export function renderSafeMarkdown(markdown: string): string {
  const rendered = marked.parse(markdown, { async: false }) as string;
  return DOMPurify.sanitize(rendered, {
    FORBID_TAGS: ['style'],
    USE_PROFILES: { html: true },
  });
}

export function renderSafeDirectiveMarkdown(markdown: string): string {
  return renderSafeMarkdown(markdown);
}
