import DOMPurify from 'dompurify';
import { marked } from 'marked';

export function normalizeMarkdownHtmlTables(markdown: string): string {
  const lines = markdown.split(/\r?\n/);
  const normalized: string[] = [];

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const trimmed = line.trim();
    const previousLine = normalized.at(-1);

    if (
      /^<table\b/i.test(trimmed)
      && previousLine !== undefined
      && previousLine.trim() !== ''
    ) {
      normalized.push('');
    }

    normalized.push(line);

    const nextLine = lines[index + 1];
    if (
      /<\/table>\s*$/i.test(trimmed)
      && nextLine !== undefined
      && nextLine.trim() !== ''
    ) {
      normalized.push('');
    }
  }

  return normalized.join('\n');
}

export function renderSafeMarkdown(markdown: string): string {
  const normalizedMarkdown = normalizeMarkdownHtmlTables(markdown);
  const rendered = marked.parse(normalizedMarkdown, { async: false }) as string;
  return DOMPurify.sanitize(rendered, {
    FORBID_TAGS: ['style'],
    USE_PROFILES: { html: true },
  });
}

export function renderSafeDirectiveMarkdown(markdown: string): string {
  return renderSafeMarkdown(markdown);
}
