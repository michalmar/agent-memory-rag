import type { CitationSource } from './client.js';

const MAX_DIRECTIVE_ID_LENGTH = 128;
const MAX_DIRECTIVE_VERSION_LENGTH = 64;
const MAX_DIRECTIVE_VERSION_ID_LENGTH = 200;
const DIRECTIVE_VERSION_ID_PREFIX = ':v';
const DIRECTIVE_VERSION = /^\d+(?:\.\d+)?$/;
const SEPARATOR_SPACING = /\s*([/._-])\s*/gu;

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
  if (!citation.directive_id || !citation.directive_version_id) {
    return null;
  }
  try {
    const directiveId = normalizeDirectiveId(citation.directive_id);
    const directiveVersionId = validateDirectiveVersionId(
      citation.directive_version_id,
      directiveId,
    );
    if (citation.version_label !== undefined) {
      const versionLabel = normalizeDirectiveVersion(citation.version_label);
      const versionInId = directiveVersionId.slice(
        directiveVersionId.lastIndexOf(DIRECTIVE_VERSION_ID_PREFIX)
        + DIRECTIVE_VERSION_ID_PREFIX.length,
      );
      if (versionLabel !== versionInId) return null;
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
  } catch (error) {
    if (error instanceof Error) return null;
    throw error;
  }
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

export function directiveDocumentPath(
  directiveId: string,
  directiveVersionId: string,
): string {
  return `/directives/document?${directiveQuery(
    directiveId,
    directiveVersionId,
  )}`;
}

export function directiveSourcePath(
  directiveId: string,
  directiveVersionId: string,
): string {
  return `/directives/source?${directiveQuery(
    directiveId,
    directiveVersionId,
  )}`;
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

export function normalizeDirectiveId(value: string): string {
  if (typeof value !== 'string') {
    throw new TypeError('Directive ID must be a string');
  }
  let normalized = value.normalize('NFKC');
  if (/\p{Cc}/u.test(normalized)) {
    throw new Error('Directive ID must not contain control characters');
  }
  normalized = normalized.trim().replace(/\s+/gu, ' ');
  normalized = normalized.replace(SEPARATOR_SPACING, '$1');
  if (!normalized) {
    throw new Error('Directive ID must not be empty');
  }
  if (Array.from(normalized).length > MAX_DIRECTIVE_ID_LENGTH) {
    throw new Error(
      `Directive ID must not exceed ${MAX_DIRECTIVE_ID_LENGTH} characters`,
    );
  }
  if (normalized.includes(':')) {
    throw new Error("Directive ID must not contain ':'");
  }
  if (!/^[\p{L}\p{Nd} /._-]+$/u.test(normalized)) {
    throw new Error(
      'Directive ID may contain only Unicode letters, digits, spaces, /._- separators',
    );
  }
  return normalized;
}

export function normalizeDirectiveVersion(value: string): string {
  if (
    typeof value !== 'string'
    || !value
    || Array.from(value).length > MAX_DIRECTIVE_VERSION_LENGTH
    || !DIRECTIVE_VERSION.test(value)
  ) {
    throw new Error(
      'Directive version must match digits with an optional decimal fraction',
    );
  }
  const [wholePart, fractionPart = ''] = value.split('.');
  const normalizedWhole = wholePart.replace(/^0+/, '') || '0';
  const fraction = fractionPart.replace(/0+$/, '');
  return fraction ? `${normalizedWhole}.${fraction}` : normalizedWhole;
}

export function buildDirectiveVersionId(
  directiveId: string,
  version: string,
): string {
  const value = `${normalizeDirectiveId(directiveId)}${DIRECTIVE_VERSION_ID_PREFIX}${
    normalizeDirectiveVersion(version)
  }`;
  if (Array.from(value).length > MAX_DIRECTIVE_VERSION_ID_LENGTH) {
    throw new Error('Directive version ID exceeds the 200-character contract limit');
  }
  return value;
}

export function validateDirectiveVersionId(
  value: string,
  directiveId?: string,
): string {
  if (
    typeof value !== 'string'
    || !value
    || Array.from(value).length > MAX_DIRECTIVE_VERSION_ID_LENGTH
  ) {
    throw new Error('Directive version ID must be 1..200 characters');
  }
  const marker = value.lastIndexOf(DIRECTIVE_VERSION_ID_PREFIX);
  if (marker <= 0) {
    throw new Error("Directive version ID must use '<directive_id>:v<version>'");
  }
  const embeddedId = value.slice(0, marker);
  const embeddedVersion = value.slice(
    marker + DIRECTIVE_VERSION_ID_PREFIX.length,
  );
  if (value !== buildDirectiveVersionId(embeddedId, embeddedVersion)) {
    throw new Error('Directive version ID is not canonical');
  }
  if (
    directiveId !== undefined
    && value !== buildDirectiveVersionId(directiveId, embeddedVersion)
  ) {
    throw new Error('Directive version ID does not belong to directive ID');
  }
  return value;
}

function directiveQuery(
  directiveId: string,
  directiveVersionId: string,
): string {
  return new URLSearchParams({
    directive_id: directiveId,
    directive_version_id: directiveVersionId,
  }).toString();
}

export function directivePdfDownloadFilename(
  documentSourceFilename: string | undefined,
  pdfFilename: string | null,
): string | null {
  return documentSourceFilename ?? pdfFilename;
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
