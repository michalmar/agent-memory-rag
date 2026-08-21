import { describe, expect, it } from 'vitest';

import {
  extractToolCitations,
  findCitationByIdentity,
  findCitationBySearchIndex,
  groupCitationsByDocument,
  mergeCitations,
  parseCitations,
  replaceCitationMarkers,
  resolveCitationIndex,
  selectCitations,
  sourceCitationEntries,
} from './citations.js';

describe('citation utilities', () => {
  it('parses envelope and direct citation arrays', () => {
    const citation = {
      ref_id: 'policy',
      source_name: 'Returns policy',
      search_idx: 2,
      url: 'https://example.test/policy',
    };

    expect(parseCitations({ data: { citations: [citation] } })).toEqual([
      citation,
    ]);
    expect(parseCitations([citation])).toEqual([citation]);
    expect(parseCitations([{ ref_id: 'missing-name' }, null])).toEqual([]);
    expect(extractToolCitations('not-json')).toEqual([]);
    expect(parseCitations([{
      ...citation,
      citation_scope: 'document',
    }])[0].citation_scope).toBe('document');
  });

  it('merges duplicates while filling missing optional fields', () => {
    const current = [{ ref_id: 'policy', source_name: 'Returns policy' }];
    const merged = mergeCitations(current, [
      {
        ref_id: 'policy',
        source_name: 'Returns policy',
        search_idx: 3,
        url: 'https://example.test/policy',
      },
      { ref_id: 'order', source_name: 'Order source' },
    ]);

    expect(merged).toEqual([
      {
        ref_id: 'policy',
        source_name: 'Returns policy',
        search_idx: 3,
        url: 'https://example.test/policy',
      },
      { ref_id: 'order', source_name: 'Order source' },
    ]);
    expect(current).toEqual([
      { ref_id: 'policy', source_name: 'Returns policy' },
    ]);
  });

  it('shares marker parsing while preserving caller-specific formatting', () => {
    const citations = [
      {
        ref_id: 'policy',
        source_name: 'Returns policy',
        search_idx: 4,
      },
    ];
    const text = 'See 【4:policy†Returns policy】.';

    const replaced = replaceCitationMarkers(text, (marker) => {
      expect(findCitationByIdentity(citations, marker)).toBe(0);
      expect(findCitationBySearchIndex(citations, marker)).toBe(0);
      return '[1]';
    });

    expect(replaced).toBe('See [1].');
  });

  it('does not use an ambiguous source name ahead of an exact search index', () => {
    const citations = [
      {
        ref_id: 'section-1',
        source_name: 'Travel policy',
        search_idx: 4,
      },
      {
        ref_id: 'section-2',
        source_name: 'Travel policy',
        search_idx: 7,
      },
    ];
    const marker = {
      searchIndex: 7,
      refId: 'unknown',
      sourceName: 'Travel policy',
    };

    expect(findCitationByIdentity(citations, marker)).toBe(-1);
    expect(resolveCitationIndex(citations, marker, 0)).toBe(1);
  });

  it('replaces Foundry citation markers using directive reference IDs', () => {
    const refId =
      '30336958:v1:s0003-3-authorization-and-driver-competence';
    const citations = [
      {
        ref_id: refId,
        source_name: 'Company car driver safety requirements',
      },
    ];
    const text = `Stop driving if your licence becomes invalid. cite${refId}`;

    const replaced = replaceCitationMarkers(text, (marker) => {
      expect(marker).toEqual({
        refId,
        sourceName: refId,
      });
      expect(findCitationByIdentity(citations, marker)).toBe(0);
      expect(findCitationBySearchIndex(citations, marker)).toBe(-1);
      return '[1]';
    });

    expect(replaced).toBe(
      'Stop driving if your licence becomes invalid. [1]',
    );

    expect(
      replaceCitationMarkers(
        `Compare cite${refId}related-section`,
        (marker) => `[${marker.refId}]`,
      ),
    ).toBe(`Compare [${refId}][related-section]`);
  });

  it('replaces plain directive citation markers', () => {
    const refId = '30336958:v1:s0003-change-requests';
    const citations = [
      {
        ref_id: refId,
        source_name: 'Microsoft 365 policy',
      },
    ];

    expect(
      replaceCitationMarkers(
        `Submit the request through ServiceDesk. [[cite:${refId}]]`,
        (marker) => {
          expect(resolveCitationIndex(citations, marker, 0)).toBe(0);
          return '[1]';
        },
      ),
    ).toBe('Submit the request through ServiceDesk. [1]');
  });

  it('keeps the authoritative citation snapshot in backend order', () => {
    const citations = [
      { ref_id: 'section-1', source_name: 'Travel policy', search_idx: 1 },
      { ref_id: 'section-2', source_name: 'AI policy', search_idx: 2 },
    ];

    expect(selectCitations('Answer without markers.', citations)).toEqual({
      citations,
      markerIndexes: [],
    });
  });

  it('resolves marker positions without filtering the final snapshot', () => {
    const citations = [
      { ref_id: 'first', source_name: 'First source', search_idx: 4 },
      { ref_id: 'second', source_name: 'Second source', search_idx: 7 },
    ];

    expect(
      selectCitations(
        'Known 【7:unknown†Unknown】 and exact citefirst.',
        citations,
      ),
    ).toEqual({
      citations,
      markerIndexes: [1, 0],
    });
  });

  it('requires exact ref IDs for directive markers', () => {
    const citations = [
      { ref_id: 'first', source_name: 'First source', search_idx: 4 },
      { ref_id: 'second', source_name: 'Second source', search_idx: 7 },
    ];

    expect(
      selectCitations(
        'Unknown 【7:not-a-ref†Second source】 and exact [first].',
        citations,
        true,
      ),
    ).toEqual({
      citations,
      markerIndexes: [-1, 0],
    });
  });

  it('replaces an opaque SHA-256 ref marker exactly', () => {
    const refId =
      '012869405198d310ea60607d3454a4823eefd02eec8c995750639d26fc8afd5';
    const text = `Grounded answer [${refId}]`;

    expect(
      replaceCitationMarkers(text, (marker) => {
        expect(marker.refId).toBe(refId);
        return '[1]';
      }),
    ).toBe('Grounded answer [1]');
  });

  it('replaces a bare corner-bracket ref marker exactly', () => {
    const refId =
      '012869405198d310ea60607d3454a4823eefdf02eec8c995750639d26fc8afd5';

    expect(
      replaceCitationMarkers(`Grounded answer 【${refId}】`, (marker) => {
        expect(marker.refId).toBe(refId);
        return '[1]';
      }),
    ).toBe('Grounded answer [1]');
  });

  it('preserves directive lineage and does not collapse distinct sections', () => {
    const parsed = parseCitations([
      {
        ref_id: 'DIR-1:v2',
        source_name: 'Travel directive',
        directive_id: 'DIR-1',
        directive_version_id: 'DIR-1:v2',
        version_label: '2.0',
        section_id: 's1',
        section_number: '1',
        section_title: 'Eligibility',
        page_from: 4,
        page_to: 5,
        effective_from: '2026-01-01',
      },
    ]);
    expect(parsed[0]).toMatchObject({
      directive_id: 'DIR-1',
      section_id: 's1',
      page_from: 4,
      mandatory_status: 'unknown',
    });

    const merged = mergeCitations(parsed, [
      {
        ...parsed[0],
        mandatory_status: 'mandatory',
        mandate_snapshot_id: 'snapshot-1',
      },
      {
        ...parsed[0],
        section_id: 's2',
        section_number: '2',
        page_from: 6,
        page_to: 7,
      },
    ]);
    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({
      mandatory_status: 'mandatory',
      mandate_snapshot_id: 'snapshot-1',
    });
    expect(merged[1].section_id).toBe('s2');
  });

  it('groups chunk citations by parent document and keeps versions distinct', () => {
    const documents = groupCitationsByDocument([
      {
        ref_id: 'policy-v2-s1',
        source_name: 'Travel policy',
        directive_id: 'DIR-1',
        directive_version_id: 'DIR-1:v2',
        version_label: '2.0',
        section_id: 's1',
        mandatory_status: 'unknown',
      },
      {
        ref_id: 'policy-v2-s2',
        source_name: 'Travel policy',
        directive_id: 'DIR-1',
        directive_version_id: 'DIR-1:v2',
        version_label: '2.0',
        section_id: 's2',
        effective_from: '2026-01-01',
        mandatory_status: 'mandatory',
      },
      {
        ref_id: 'policy-v1-s1',
        source_name: 'Travel policy',
        directive_id: 'DIR-1',
        directive_version_id: 'DIR-1:v1',
        version_label: '1.0',
        mandatory_status: 'non_mandatory',
      },
    ]);

    expect(documents).toHaveLength(2);
    expect(documents[0]).toMatchObject({
      firstSourceIndex: 0,
      sourceCount: 2,
      citation: {
        directive_version_id: 'DIR-1:v2',
        effective_from: '2026-01-01',
        mandatory_status: 'mandatory',
      },
    });

    expect(documents[1]).toMatchObject({
      firstSourceIndex: 2,
      sourceCount: 1,
      citation: {
        directive_version_id: 'DIR-1:v1',
        mandatory_status: 'non_mandatory',
      },
    });
  });

  it('keeps document references out of the Sources list', () => {
    const document = {
      ref_id: 'DIR-1:v2',
      source_name: 'Travel policy',
      directive_id: 'DIR-1',
      directive_version_id: 'DIR-1:v2',
      citation_scope: 'document' as const,
    };
    const source = {
      ...document,
      ref_id: 'DIR-1:v2:s8',
      section_id: 's8',
      citation_scope: 'source' as const,
    };

    expect(sourceCitationEntries([document, source])).toEqual([
      { citation: source, index: 1 },
    ]);
    expect(groupCitationsByDocument([document])[0].sourceCount).toBe(0);
  });

  it('groups non-directive chunks by source name', () => {
    const documents = groupCitationsByDocument([
      { ref_id: 'chunk-1', source_name: 'Benefits handbook' },
      { ref_id: 'chunk-2', source_name: 'Benefits handbook' },
      { ref_id: 'chunk-3', source_name: 'Expense guide' },
    ]);

    expect(documents.map(({ citation, sourceCount }) => ({
      name: citation.source_name,
      sourceCount,
    }))).toEqual([
      { name: 'Benefits handbook', sourceCount: 2 },
      { name: 'Expense guide', sourceCount: 1 },
    ]);
  });

  it('marks conflicting parent-document statuses as unknown', () => {
    const [document] = groupCitationsByDocument([
      {
        ref_id: 'policy-s1',
        source_name: 'Travel policy',
        directive_id: 'DIR-1',
        directive_version_id: 'DIR-1:v2',
        mandatory_status: 'mandatory',
      },
      {
        ref_id: 'policy-s2',
        source_name: 'Travel policy',
        directive_id: 'DIR-1',
        directive_version_id: 'DIR-1:v2',
        mandatory_status: 'non_mandatory',
      },
    ]);

    expect(document.citation.mandatory_status).toBe('unknown');
  });
});
