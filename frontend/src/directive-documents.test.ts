import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  directiveOpenRequestFromCitation,
  directiveDocumentPath,
  directivePdfDownloadFilename,
  directiveSourcePath,
  isAbortError,
  LatestRequest,
  locateDirectiveHeading,
  normalizeDirectiveId,
  normalizeDirectiveVersion,
  parseDirectiveSectionOrdinal,
  pdfUrlForPage,
  sameDirectiveDocument,
  toDirectiveCitationTarget,
  toDirectiveDocumentReference,
  validateDirectiveVersionId,
} from './directive-documents.js';

describe('directive document helpers', () => {
  it('creates references only for exact directive versions', () => {
    expect(
      toDirectiveDocumentReference({
        ref_id: 'section-1',
        source_name: 'Driver safety',
        directive_id: '30336958',
        directive_version_id: '30336958:v1',
        version_label: '1.0',
        page_from: 3,
      }),
    ).toEqual({
      directiveId: '30336958',
      directiveVersionId: '30336958:v1',
      sourceName: 'Driver safety',
      versionLabel: '1.0',
    });
    expect(
      toDirectiveDocumentReference({
        ref_id: 'external',
        source_name: 'External source',
        url: 'https://example.test/document',
      }),
    ).toBeNull();
    expect(
      toDirectiveDocumentReference({
        ref_id: 'mismatch',
        source_name: 'Wrong version',
        directive_id: '30336958',
        directive_version_id: '72403881:v1',
      }),
    ).toBeNull();
  });

  it('maps citation metadata separately from exact document identity', () => {
    const citation = {
      ref_id: 'section-3',
      source_name: 'Driver safety',
      directive_id: '30336958',
      directive_version_id: '30336958:v1',
      version_label: '1.0',
      section_id: 's0003-authorization-and-driver-competence',
      section_number: '3',
      section_title: 'Authorization and driver competence',
      page_from: 4,
      page_to: 5,
    };

    expect(toDirectiveCitationTarget(citation, 2)).toEqual({
      sectionId: 's0003-authorization-and-driver-competence',
      sectionNumber: '3',
      sectionTitle: 'Authorization and driver competence',
      pageFrom: 4,
      pageTo: 5,
      sourceIndex: 2,
    });
    expect(directiveOpenRequestFromCitation(citation, 2)).toEqual({
      reference: {
        directiveId: '30336958',
        directiveVersionId: '30336958:v1',
        sourceName: 'Driver safety',
        versionLabel: '1.0',
      },
      target: {
        sectionId: 's0003-authorization-and-driver-competence',
        sectionNumber: '3',
        sectionTitle: 'Authorization and driver competence',
        pageFrom: 4,
        pageTo: 5,
        sourceIndex: 2,
      },
      initialTab: 'document',
    });
  });

  it('normalizes Unicode IDs and validates numeric version ownership', () => {
    expect(normalizeDirectiveId('  číslo  /  7 ')).toBe('ČÍSLO/7');
    expect(normalizeDirectiveId('İSTANBUL / ß')).toBe('İSTANBUL/SS');
    expect(normalizeDirectiveId('cafe\u0301 _ 2')).toBe('CAFÉ_2');
    expect(normalizeDirectiveId('ƛ/7')).toBe('ƛ/7');
    expect(normalizeDirectiveId('ɤ/7')).toBe('ɤ/7');
    expect(normalizeDirectiveId('ꟓ/7')).toBe('ꟓ/7');
    expect(normalizeDirectiveId('ꟕ/7')).toBe('ꟕ/7');
    expect(normalizeDirectiveId('𐞑/7')).toBe('ɤ/7');
    expect(normalizeDirectiveId('ͺ/7')).toBe('Ι/7');
    expect(normalizeDirectiveVersion('0000.0100')).toBe('0.01');
    expect(
      toDirectiveDocumentReference({
        ref_id: 'unicode',
        source_name: 'Czech directive',
        directive_id: '  číslo  /  7 ',
        directive_version_id: 'ČÍSLO/7:v1',
        version_label: '01.0',
      }),
    ).toEqual({
      directiveId: 'ČÍSLO/7',
      directiveVersionId: 'ČÍSLO/7:v1',
      sourceName: 'Czech directive',
      versionLabel: '01.0',
    });
    expect(validateDirectiveVersionId('ČÍSLO/7:v1', ' číslo/7 ')).toBe(
      'ČÍSLO/7:v1',
    );
    expect(
      toDirectiveDocumentReference({
        ref_id: 'python-311-unicode',
        source_name: 'Unicode directive',
        directive_id: 'ƛ/7',
        directive_version_id: 'ƛ/7:v1',
      }),
    ).toMatchObject({
      directiveId: 'ƛ/7',
      directiveVersionId: 'ƛ/7:v1',
    });
  });

  it('preserves distinct long decimal versions without numeric rounding', () => {
    const directiveId = 'ČÍSLO/7';
    const firstVersion = '1234567890123456789012345678901234';
    const secondVersion = '1234567890123456789012345678901235';

    expect(normalizeDirectiveVersion(firstVersion)).toBe(firstVersion);
    expect(normalizeDirectiveVersion(secondVersion)).toBe(secondVersion);

    expect(
      toDirectiveDocumentReference({
        ref_id: 'long-version-a',
        source_name: 'Long version directive',
        directive_id: directiveId,
        directive_version_id: `${directiveId}:v${firstVersion}`,
        version_label: firstVersion,
      }),
    ).toMatchObject({
      directiveId,
      directiveVersionId: `${directiveId}:v${firstVersion}`,
      versionLabel: firstVersion,
    });
    expect(
      directiveSourcePath(
        directiveId,
        `${directiveId}:v${secondVersion}`,
      ),
    ).toBe(
      `/directives/source?directive_id=%C4%8C%C3%8DSLO%2F7&directive_version_id=%C4%8C%C3%8DSLO%2F7%3Av${secondVersion}`,
    );
    expect(
      toDirectiveDocumentReference({
        ref_id: 'long-version-b',
        source_name: 'Long version directive',
        directive_id: directiveId,
        directive_version_id: `${directiveId}:v${secondVersion}`,
        version_label: secondVersion,
      }),
    ).toMatchObject({
      directiveId,
      directiveVersionId: `${directiveId}:v${secondVersion}`,
      versionLabel: secondVersion,
    });
    expect(
      toDirectiveDocumentReference({
        ref_id: 'long-version-mismatch',
        source_name: 'Long version directive',
        directive_id: directiveId,
        directive_version_id: `${directiveId}:v${secondVersion}`,
        version_label: firstVersion,
      }),
    ).toBeNull();
  });

  it('rejects invalid, mismatched, and non-canonical identities', () => {
    const base = {
      ref_id: 'invalid',
      source_name: 'Directive',
      directive_id: 'DIR/7',
      directive_version_id: 'DIR/8:v1',
    };
    expect(toDirectiveDocumentReference(base)).toBeNull();
    expect(
      toDirectiveDocumentReference({
        ...base,
        directive_version_id: 'DIR/7:v1:extra',
      }),
    ).toBeNull();
    expect(
      toDirectiveDocumentReference({
        ...base,
        directive_version_id: 'DIR/7:v1',
        version_label: 'stable',
      }),
    ).toBeNull();
    expect(
      toDirectiveDocumentReference({
        ...base,
        directive_id: 'DIR:7',
        directive_version_id: 'DIR:7:v1',
      }),
    ).toBeNull();
    expect(
      toDirectiveDocumentReference({
        ...base,
        directive_id: 'DIR\u00007',
        directive_version_id: 'DIR\u00007:v1',
      }),
    ).toBeNull();
    expect(
      toDirectiveDocumentReference({
        ...base,
        directive_id: 'D'.repeat(129),
        directive_version_id: `${'D'.repeat(129)}:v1`,
      }),
    ).toBeNull();
  });

  it('uses only authoritative PDF or document filenames for download', () => {
    expect(
      directivePdfDownloadFilename(undefined, 'český dokument.pdf'),
    ).toBe('český dokument.pdf');
    expect(directivePdfDownloadFilename(undefined, null)).toBeNull();
    expect(
      directivePdfDownloadFilename(undefined, null),
    ).not.toBe('ČÍSLO/7.pdf');
  });

  it('parses generated section ordinals and recognizes document control', () => {
    expect(parseDirectiveSectionOrdinal('s0000-document-control')).toBe(0);
    expect(parseDirectiveSectionOrdinal('s0012-driver-training')).toBe(12);
    expect(parseDirectiveSectionOrdinal('legacy-driver-training')).toBeNull();
    expect(parseDirectiveSectionOrdinal('s12-driver-training')).toBeNull();
    expect(
      locateDirectiveHeading(['1 Scope'], {
        sectionId: 's0000-document-control',
      }),
    ).toEqual({ kind: 'document-top' });
  });

  it('locates and validates generated heading ordinals', () => {
    const headings = [
      '1. Scope',
      '2 — Driver training',
      '3. Authorization and driver competence',
    ];

    expect(
      locateDirectiveHeading(headings, {
        sectionId: 's0003-authorization-and-driver-competence',
        sectionNumber: '3',
        sectionTitle: 'Authorization and driver competence',
      }),
    ).toEqual({ kind: 'heading', index: 2 });
  });

  it('uses only a unique normalized heading when ordinal validation fails', () => {
    const headings = [
      '3. Authorization and driver competence',
      '4. Vehicle requirements',
    ];

    expect(
      locateDirectiveHeading(headings, {
        sectionId: 's0002-authorization-and-driver-competence',
        sectionNumber: '3',
        sectionTitle: 'Authorization & Driver Competence',
      }),
    ).toEqual({ kind: 'heading', index: 0 });
    expect(
      locateDirectiveHeading(['1. Scope', '2. Scope'], {
        sectionTitle: 'Scope',
      }),
    ).toEqual({ kind: 'unavailable' });
    expect(
      locateDirectiveHeading(headings, {
        sectionId: 'legacy-section',
      }),
    ).toEqual({ kind: 'unavailable' });
  });

  it('compares directive identity independently of targets and tabs', () => {
    const reference = {
      directiveId: '30336958',
      directiveVersionId: '30336958:v1',
      sourceName: 'Driver safety',
    };
    expect(sameDirectiveDocument(reference, { ...reference })).toBe(true);
    expect(
      sameDirectiveDocument(reference, {
        ...reference,
        directiveVersionId: '30336958:v2',
      }),
    ).toBe(false);
  });

  it('encodes exact-version query routes and targets the cited page', () => {
    expect(directiveDocumentPath(' číslo / 7 ', 'ČÍSLO/7:v1')).toBe(
      '/directives/document?directive_id=%C4%8C%C3%8DSLO%2F7&directive_version_id=%C4%8C%C3%8DSLO%2F7%3Av1',
    );
    expect(directiveDocumentPath('ƛ/7', 'ƛ/7:v1')).toBe(
      '/directives/document?directive_id=%C6%9B%2F7&directive_version_id=%C6%9B%2F7%3Av1',
    );
    expect(directiveDocumentPath('ČÍSLO/7', 'ČÍSLO/7:v1')).toBe(
      '/directives/document?directive_id=%C4%8C%C3%8DSLO%2F7&directive_version_id=%C4%8C%C3%8DSLO%2F7%3Av1',
    );
    expect(directiveSourcePath('ČÍSLO/7', 'ČÍSLO/7:v1')).toBe(
      '/directives/source?directive_id=%C4%8C%C3%8DSLO%2F7&directive_version_id=%C4%8C%C3%8DSLO%2F7%3Av1',
    );
    expect(pdfUrlForPage('blob:https://app.test/pdf', 3)).toBe(
      'blob:https://app.test/pdf#page=3',
    );
  });

  it('invalidates stale requests without coupling document and PDF loads', () => {
    const documents = new LatestRequest();
    const pdfs = new LatestRequest();
    const firstDocument = documents.begin();
    const pdf = pdfs.begin();
    const secondDocument = documents.begin();

    expect(documents.isCurrent(firstDocument)).toBe(false);
    expect(documents.isCurrent(secondDocument)).toBe(true);
    expect(pdfs.isCurrent(pdf)).toBe(true);

    pdfs.invalidate();
    expect(pdfs.isCurrent(pdf)).toBe(false);
  });

  it('recognizes browser and structural abort errors', () => {
    expect(isAbortError(new DOMException('cancelled', 'AbortError'))).toBe(true);
    expect(isAbortError({ name: 'AbortError' })).toBe(true);
    expect(isAbortError(new Error('failed'))).toBe(false);
  });
});

describe('directive document client', () => {
  const storage = new Map<string, string>();
  const fetchMock = vi.fn<
    (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  >();

  beforeEach(() => {
    storage.clear();
    storage.set('mockUserId', 'user-alice');
    fetchMock.mockReset();
    vi.stubGlobal('window', {
      __APP_CONFIG__: {
        apiBaseUrl: '/api',
        authMode: 'mock',
      },
      location: { search: '' },
    });
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses authenticated JSON and PDF requests', async () => {
    const { AGUIClient } = await import('./client.js');
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            directive_id: '30336958',
            directive_version_id: '30336958:v1',
            title: 'Driver safety',
            version_label: '1.0',
            effective_from: '2025-01-01',
            source_filename: 'driver-safety.pdf',
            total_pages: 4,
            markdown: '# Driver safety',
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response('%PDF-content', {
          status: 200,
          headers: {
            'Content-Type': 'application/pdf',
            'Content-Disposition':
              "attachment; filename*=UTF-8''%C4%8Desk%C3%BD%20dokument.pdf",
          },
        }),
      );
    const client = new AGUIClient();
    const controller = new AbortController();

    const document = await client.getDirectiveDocument(
      'ČÍSLO/7',
      'ČÍSLO/7:v1',
      controller.signal,
    );
    const pdf = await client.getDirectiveSourcePdf(
      'ČÍSLO/7',
      'ČÍSLO/7:v1',
      controller.signal,
    );

    expect(document.title).toBe('Driver safety');
    expect(pdf.blob.type).toBe('application/pdf');
    expect(pdf.filename).toBe('český dokument.pdf');
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/directives/document?directive_id=%C4%8C%C3%8DSLO%2F7&directive_version_id=%C4%8C%C3%8DSLO%2F7%3Av1',
      expect.objectContaining({
        headers: {
          Accept: 'application/json',
          'X-Mock-User-ID': 'user-alice',
        },
        signal: controller.signal,
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/directives/source?directive_id=%C4%8C%C3%8DSLO%2F7&directive_version_id=%C4%8C%C3%8DSLO%2F7%3Av1',
      expect.objectContaining({
        headers: {
          Accept: 'application/pdf',
          'X-Mock-User-ID': 'user-alice',
        },
        signal: controller.signal,
      }),
    );
  });

  it('keeps the authoritative PDF filename when document loading fails', async () => {
    const { AGUIClient } = await import('./client.js');
    fetchMock
      .mockResolvedValueOnce(new Response('missing', { status: 404 }))
      .mockResolvedValueOnce(
        new Response('%PDF-content', {
          status: 200,
          headers: {
            'Content-Type': 'application/pdf',
            'Content-Disposition': 'attachment; filename="skutecny zdroj.pdf"',
          },
        }),
      );
    const client = new AGUIClient();

    await expect(
      client.getDirectiveDocument('ČÍSLO/7', 'ČÍSLO/7:v1'),
    ).rejects.toThrow();
    const pdf = await client.getDirectiveSourcePdf('ČÍSLO/7', 'ČÍSLO/7:v1');

    expect(pdf.filename).toBe('skutecny zdroj.pdf');
    expect(directivePdfDownloadFilename(undefined, pdf.filename ?? null)).toBe(
      'skutecny zdroj.pdf',
    );
    expect(
      directivePdfDownloadFilename(undefined, pdf.filename ?? null),
    ).not.toBe('ČÍSLO/7.pdf');
  });

  it('rejects non-PDF responses before creating an object URL', async () => {
    const { AGUIClient } = await import('./client.js');
    fetchMock.mockResolvedValueOnce(
      new Response('<html>sign in</html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      }),
    );
    const client = new AGUIClient();

    await expect(
      client.getDirectiveSourcePdf('ČÍSLO/7', 'ČÍSLO/7:v1'),
    ).rejects.toThrow('returned text/html');
  });
});
