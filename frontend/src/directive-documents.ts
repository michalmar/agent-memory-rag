import type { CitationSource } from './client.js';

const DIRECTIVE_ID = /^\d{8}$/;
const DIRECTIVE_VERSION_ID = /^\d{8}:v\d+(?:\.\d+)?$/;
const RELATIVE_DIRECTIVE_PDF =
  /(?:^|\/)(\d{8})-[^/?#]+-v(\d+(?:\.\d+)?)\.pdf(?:[?#].*)?$/i;

export type DirectiveDocumentTab = 'document' | 'pdf';
export type DirectiveDocumentLoadStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'error';

export interface DirectiveDocumentReference {
  directiveId: string;
  directiveVersionId: string;
  sourceName: string;
  versionLabel?: string;
  effectiveFrom?: string;
  pageFrom?: number;
}

export class LatestRequest {
  private generation = 0;

  begin(): number {
    return ++this.generation;
  }

  isCurrent(request: number): boolean {
    return request === this.generation;
  }

  invalidate(): void {
    ++this.generation;
  }
}

export function toDirectiveDocumentReference(
  citation: CitationSource,
): DirectiveDocumentReference | null {
  const directiveId = citation.directive_id;
  const directiveVersionId = citation.directive_version_id;
  if (
    !directiveId
    || !directiveVersionId
    || !DIRECTIVE_ID.test(directiveId)
    || !DIRECTIVE_VERSION_ID.test(directiveVersionId)
    || !directiveVersionId.startsWith(`${directiveId}:`)
  ) {
    return null;
  }
  return {
    directiveId,
    directiveVersionId,
    sourceName: citation.source_name,
    ...(citation.version_label
      ? { versionLabel: citation.version_label }
      : {}),
    ...(citation.effective_from
      ? { effectiveFrom: citation.effective_from }
      : {}),
    ...(citation.page_from && citation.page_from >= 1
      ? { pageFrom: citation.page_from }
      : {}),
  };
}

export function directiveReferenceFromPdfHref(
  href: string,
  sourceName: string,
): DirectiveDocumentReference | null {
  const value = href.trim();
  if (
    !value
    || /^[a-z][a-z\d+.-]*:/i.test(value)
    || value.startsWith('//')
  ) {
    return null;
  }
  const match = RELATIVE_DIRECTIVE_PDF.exec(value);
  if (!match) return null;

  const directiveId = match[1];
  const versionLabel = match[2];
  const normalizedVersion = normalizeVersionLabel(versionLabel);
  return {
    directiveId,
    directiveVersionId: `${directiveId}:v${normalizedVersion}`,
    sourceName: sourceName.trim() || `${directiveId} directive`,
    versionLabel,
  };
}

export function directiveDocumentPath(
  directiveId: string,
  directiveVersionId: string,
): string {
  return `/directives/${encodeURIComponent(directiveId)}/versions/${encodeURIComponent(
    directiveVersionId,
  )}/document`;
}

export function directiveSourcePath(
  directiveId: string,
  directiveVersionId: string,
): string {
  return `/directives/${encodeURIComponent(directiveId)}/versions/${encodeURIComponent(
    directiveVersionId,
  )}/source`;
}

export function pdfUrlForPage(objectUrl: string, pageFrom?: number): string {
  if (!pageFrom || pageFrom < 1) return objectUrl;
  return `${objectUrl}#page=${Math.floor(pageFrom)}`;
}

export function isAbortError(error: unknown): boolean {
  return (
    error instanceof DOMException && error.name === 'AbortError'
  ) || (
    typeof error === 'object'
    && error !== null
    && 'name' in error
    && error.name === 'AbortError'
  );
}

function normalizeVersionLabel(versionLabel: string): string {
  const [wholePart, fractionPart = ''] = versionLabel.split('.');
  const whole = wholePart.replace(/^0+(?=\d)/, '');
  const fraction = fractionPart.replace(/0+$/, '');
  return fraction ? `${whole}.${fraction}` : whole;
}
