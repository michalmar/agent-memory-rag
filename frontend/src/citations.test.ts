import { describe, expect, it } from 'vitest';

import {
  extractToolCitations,
  findCitationByIdentity,
  findCitationBySearchIndex,
  groupCitationsByDocument,
  mergeCitations,
  parseCitations,
  replaceCitationMarkers,
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
