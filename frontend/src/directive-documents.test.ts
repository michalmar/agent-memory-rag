import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  directiveOpenRequestFromCitation,
  directiveDocumentPath,
  directiveReferenceFromPdfHref,
  directiveSourcePath,
  isAbortError,
  LatestRequest,
  locateDirectiveHeading,
  parseDirectiveSectionOrdinal,
  pdfUrlForPage,
  sameDirectiveDocument,
  toDirectiveCitationTarget,
  toDirectiveDocumentReference,
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

  it('encodes exact-version API paths and targets the cited page', () => {
    expect(directiveDocumentPath('30336958', '30336958:v1')).toBe(
      '/directives/30336958/versions/30336958%3Av1/document',
    );
    expect(directiveSourcePath('30336958', '30336958:v1')).toBe(
      '/directives/30336958/versions/30336958%3Av1/source',
    );
    expect(pdfUrlForPage('blob:https://app.test/pdf', 3)).toBe(
      'blob:https://app.test/pdf#page=3',
    );
  });

  it('maps relative Markdown PDF links back to exact directive versions', () => {
    expect(
      directiveReferenceFromPdfHref(
        '../pdf/30336958-company-car-driver-safety-requirements-v1.0.pdf',
        'Company Car Driver Safety Requirements',
      ),
    ).toEqual({
      directiveId: '30336958',
      directiveVersionId: '30336958:v1',
      sourceName: 'Company Car Driver Safety Requirements',
      versionLabel: '1.0',
    });
    expect(
      directiveReferenceFromPdfHref(
        'https://example.test/30336958-policy-v1.pdf',
        'External PDF',
      ),
    ).toBeNull();
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
          headers: { 'Content-Type': 'application/pdf' },
        }),
      );
    const client = new AGUIClient();
    const controller = new AbortController();

    const document = await client.getDirectiveDocument(
      '30336958',
      '30336958:v1',
      controller.signal,
    );
    const pdf = await client.getDirectiveSourcePdf(
      '30336958',
      '30336958:v1',
      controller.signal,
    );

    expect(document.title).toBe('Driver safety');
    expect(pdf.type).toBe('application/pdf');
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/directives/30336958/versions/30336958%3Av1/document',
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
      '/api/directives/30336958/versions/30336958%3Av1/source',
      expect.objectContaining({
        headers: {
          Accept: 'application/pdf',
          'X-Mock-User-ID': 'user-alice',
        },
        signal: controller.signal,
      }),
    );
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
      client.getDirectiveSourcePdf('30336958', '30336958:v1'),
    ).rejects.toThrow('returned text/html');
  });
});
