import DOMPurify from 'dompurify';
import { marked } from 'marked';

import { directiveReferenceFromPdfHref } from './directive-documents.js';

export function renderSafeMarkdown(markdown: string): string {
  const rendered = marked.parse(markdown, { async: false }) as string;
  return DOMPurify.sanitize(rendered, {
    FORBID_TAGS: ['style'],
    USE_PROFILES: { html: true },
  });
}

export function renderSafeDirectiveMarkdown(markdown: string): string {
  const document = new DOMParser().parseFromString(
    renderSafeMarkdown(markdown),
    'text/html',
  );
  for (const link of document.querySelectorAll<HTMLAnchorElement>('a[href]')) {
    const href = link.getAttribute('href');
    if (
      !href
      || !directiveReferenceFromPdfHref(href, link.textContent ?? '')
    ) {
      continue;
    }

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'document-markdown-link';
    button.dataset.directivePdfHref = href;
    button.setAttribute(
      'aria-label',
      `Open ${link.textContent?.trim() || 'directive document'}`,
    );
    button.append(...Array.from(link.childNodes));
    link.replaceWith(button);
  }
  return document.body.innerHTML;
}
