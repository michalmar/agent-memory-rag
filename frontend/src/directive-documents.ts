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
}

export interface DirectiveCitationTarget {
  sectionId?: string;
  sectionNumber?: string;
  sectionTitle?: string;
  pageFrom?: number;
  pageTo?: number;
  sourceIndex?: number;
}

export interface DirectiveDocumentOpenRequest {
  reference: DirectiveDocumentReference;
  target?: DirectiveCitationTarget;
  initialTab: DirectiveDocumentTab;
}

export type DirectiveHeadingLocation =
  | { kind: 'document-top' }
  | { kind: 'heading'; index: number }
  | { kind: 'unavailable' };

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
  };
}

export function toDirectiveCitationTarget(
  citation: CitationSource,
  sourceIndex?: number,
): DirectiveCitationTarget {
  const sectionId = cleanText(citation.section_id);
  const sectionNumber = cleanText(citation.section_number);
  const sectionTitle = cleanText(citation.section_title);
  const pageFrom = positiveInteger(citation.page_from);
  const pageTo = positiveInteger(citation.page_to);
  const validSourceIndex =
    typeof sourceIndex === 'number'
    && Number.isInteger(sourceIndex)
    && sourceIndex >= 0;
  return {
    ...(sectionId ? { sectionId } : {}),
    ...(sectionNumber ? { sectionNumber } : {}),
    ...(sectionTitle ? { sectionTitle } : {}),
    ...(pageFrom ? { pageFrom } : {}),
    ...(pageFrom && pageTo && pageTo >= pageFrom ? { pageTo } : {}),
    ...(validSourceIndex ? { sourceIndex } : {}),
  };
}

export function directiveOpenRequestFromCitation(
  citation: CitationSource,
  sourceIndex?: number,
): DirectiveDocumentOpenRequest | null {
  const reference = toDirectiveDocumentReference(citation);
  if (!reference) return null;
  return {
    reference,
    target: toDirectiveCitationTarget(citation, sourceIndex),
    initialTab: 'document',
  };
}

export function parseDirectiveSectionOrdinal(
  sectionId?: string,
): number | null {
  const match = /^s(\d{4})-/.exec(sectionId?.trim() ?? '');
  if (!match) return null;
  return Number.parseInt(match[1], 10);
}

export function locateDirectiveHeading(
  headings: readonly string[],
  target: DirectiveCitationTarget,
): DirectiveHeadingLocation {
  const ordinal = parseDirectiveSectionOrdinal(target.sectionId);
  if (ordinal === 0) return { kind: 'document-top' };

  if (ordinal !== null) {
    const index = ordinal - 1;
    if (
      index >= 0
      && index < headings.length
      && headingMatchesTarget(headings[index], target)
    ) {
      return { kind: 'heading', index };
    }
  }

  if (!target.sectionNumber && !target.sectionTitle) {
    return { kind: 'unavailable' };
  }
  const matches = headings
    .map((heading, index) => ({ heading, index }))
    .filter(({ heading }) => headingMatchesTarget(heading, target));
  return matches.length === 1
    ? { kind: 'heading', index: matches[0].index }
    : { kind: 'unavailable' };
}

export function sameDirectiveDocument(
  left: DirectiveDocumentReference | null,
  right: DirectiveDocumentReference,
): boolean {
  return (
    left?.directiveId === right.directiveId
    && left.directiveVersionId === right.directiveVersionId
  );
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

function headingMatchesTarget(
  heading: string,
  target: DirectiveCitationTarget,
): boolean {
  const normalizedHeading = normalizeHeading(heading);
  const sectionNumber = normalizeHeading(target.sectionNumber ?? '');
  const sectionTitle = normalizeHeading(target.sectionTitle ?? '');
  if (!sectionNumber && !sectionTitle) return true;
  if (sectionNumber && sectionTitle) {
    return normalizedHeading === `${sectionNumber} ${sectionTitle}`;
  }
  if (sectionNumber) {
    return (
      normalizedHeading === sectionNumber
      || normalizedHeading.startsWith(`${sectionNumber} `)
    );
  }
  return (
    normalizedHeading === sectionTitle
    || normalizedHeading.endsWith(` ${sectionTitle}`)
  );
}

function normalizeHeading(value: string): string {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

function cleanText(value?: string): string | undefined {
  const cleaned = value?.trim();
  return cleaned || undefined;
}

function positiveInteger(value?: number): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 1
    ? Math.floor(value)
    : undefined;
}
