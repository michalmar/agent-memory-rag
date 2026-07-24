import type {
  CitationSource,
  MandatoryStatus,
} from './client.js';

const CITATION_MARKER = /【(\d+):([^†】]+)†([^】]+)】/g;
const MERGEABLE_CITATION_FIELDS = [
  'url',
  'search_idx',
  'directive_id',
  'directive_version_id',
  'version_label',
  'section_id',
  'section_number',
  'section_title',
  'page_from',
  'page_to',
  'effective_from',
  'mandatory_status',
  'mandate_snapshot_id',
  'retrieval_strategy',
  'coverage',
] as const;

export interface CitationMarker {
  searchIndex: number;
  refId: string;
  sourceName: string;
}

export interface CitationDocument {
  citation: CitationSource;
  firstSourceIndex: number;
  sourceCount: number;
}

function citationValues(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== 'object') return [];

  const envelope = value as Record<string, unknown>;
  const data =
    envelope.data && typeof envelope.data === 'object' && !Array.isArray(envelope.data)
      ? (envelope.data as Record<string, unknown>)
      : envelope;
  const values = Array.isArray(envelope.citations) ? envelope.citations : data.citations;
  return Array.isArray(values) ? values : [];
}

export function parseCitations(value: unknown): CitationSource[] {
  return citationValues(value).flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const citation = item as Record<string, unknown>;
    if (typeof citation.ref_id !== 'string' || typeof citation.source_name !== 'string') {
      return [];
    }
    const result: CitationSource = {
      ref_id: citation.ref_id,
      source_name: citation.source_name,
    };
    if (
      typeof citation.search_idx === 'number'
      && Number.isInteger(citation.search_idx)
    ) {
      result.search_idx = citation.search_idx;
    }
    if (typeof citation.url === 'string') result.url = citation.url;
    for (const field of [
      'directive_id',
      'directive_version_id',
      'version_label',
      'section_id',
      'section_number',
      'section_title',
      'effective_from',
      'mandate_snapshot_id',
      'retrieval_strategy',
    ] as const) {
      if (typeof citation[field] === 'string') result[field] = citation[field];
    }
    for (const field of ['page_from', 'page_to'] as const) {
      const page = citation[field];
      if (typeof page === 'number' && Number.isInteger(page) && page >= 0) {
        result[field] = page;
      }
    }
    const status = citation.mandatory_status;
    if (
      status === 'mandatory'
      || status === 'non_mandatory'
      || status === 'unknown'
    ) {
      result.mandatory_status = status as MandatoryStatus;
    } else if (result.directive_id) {
      result.mandatory_status = 'unknown';
    }
    if (
      citation.coverage
      && typeof citation.coverage === 'object'
      && !Array.isArray(citation.coverage)
    ) {
      result.coverage = citation.coverage as Record<string, unknown>;
    }
    return [result];
  });
}

export function extractToolCitations(content: string): CitationSource[] {
  try {
    return parseCitations(JSON.parse(content));
  } catch {
    return [];
  }
}

export function hasCitations(value: unknown): boolean {
  return citationValues(value).length > 0;
}

export function mergeCitations(
  current: CitationSource[],
  additions: CitationSource[],
): CitationSource[] {
  const merged = current.map((citation) => ({ ...citation }));
  const positions = new Map(
    merged.map((citation, index) => [
      citationKey(citation),
      index,
    ]),
  );

  for (const citation of additions) {
    const key = citationKey(citation);
    const existingIndex = positions.get(key);
    if (existingIndex !== undefined) {
      const existing = merged[existingIndex];
      if (
        existing.mandatory_status === 'unknown'
        && (
          citation.mandatory_status === 'mandatory'
          || citation.mandatory_status === 'non_mandatory'
        )
      ) {
        existing.mandatory_status = citation.mandatory_status;
      }
      for (const field of MERGEABLE_CITATION_FIELDS) {
        if (existing[field] == null && citation[field] != null) {
          Object.assign(existing, { [field]: citation[field] });
        }
      }
      continue;
    }
    positions.set(key, merged.length);
    merged.push({ ...citation });
  }
  return merged;
}

export function groupCitationsByDocument(
  citations: CitationSource[],
): CitationDocument[] {
  const documents: CitationDocument[] = [];
  const positions = new Map<string, number>();

  for (const [sourceIndex, citation] of citations.entries()) {
    const key = documentKey(citation);
    const existingIndex = positions.get(key);
    if (existingIndex === undefined) {
      positions.set(key, documents.length);
      documents.push({
        citation: { ...citation },
        firstSourceIndex: sourceIndex,
        sourceCount: 1,
      });
      continue;
    }

    const document = documents[existingIndex];
    const currentStatus = document.citation.mandatory_status;
    for (const field of MERGEABLE_CITATION_FIELDS) {
      if (
        field !== 'mandatory_status'
        && document.citation[field] == null
        && citation[field] != null
      ) {
        Object.assign(document.citation, { [field]: citation[field] });
      }
    }
    document.citation.mandatory_status = mergeMandatoryStatus(
      currentStatus,
      citation.mandatory_status,
    );
    document.sourceCount += 1;
  }

  return documents;
}

function documentKey(citation: CitationSource): string {
  if (citation.directive_version_id) {
    return `directive-version\u0000${citation.directive_version_id}`;
  }
  if (citation.directive_id) {
    return [
      'directive',
      citation.directive_id,
      citation.version_label ?? '',
    ].join('\u0000');
  }
  const name = citation.source_name
    .replace(/【[^】]+】/g, '')
    .trim()
    .toLowerCase();
  return [
    'source',
    name || citation.ref_id,
    citation.version_label ?? '',
  ].join('\u0000');
}

function mergeMandatoryStatus(
  current: MandatoryStatus | undefined,
  addition: MandatoryStatus | undefined,
): MandatoryStatus | undefined {
  if (!current || current === 'unknown') return addition ?? current;
  if (!addition || addition === 'unknown') return current;
  return current === addition ? current : 'unknown';
}

function citationKey(citation: CitationSource): string {
  const base = `${citation.ref_id}\u0000${citation.source_name}`;
  if (!citation.directive_id) return base;
  return [
    base,
    citation.directive_version_id ?? '',
    citation.section_id ?? '',
    citation.page_from ?? '',
    citation.page_to ?? '',
  ].join('\u0000');
}

export function replaceCitationMarkers(
  text: string,
  replacement: (marker: CitationMarker) => string,
): string {
  return text.replace(
    CITATION_MARKER,
    (_marker, searchIndex: string, refId: string, sourceName: string) =>
      replacement({
        searchIndex: Number(searchIndex),
        refId,
        sourceName,
      }),
  );
}

export function findCitationByIdentity(
  citations: CitationSource[],
  marker: CitationMarker,
): number {
  return citations.findIndex(
    (citation) =>
      citation.ref_id === marker.refId ||
      citation.source_name === marker.sourceName,
  );
}

export function findCitationBySearchIndex(
  citations: CitationSource[],
  marker: CitationMarker,
): number {
  return citations.findIndex(
    (citation) => citation.search_idx === marker.searchIndex,
  );
}
