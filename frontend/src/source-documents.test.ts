import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

import {
  DIRECTIVE_SOURCE_RETENTION_NOTICE,
  directiveSourceUploadError,
  validateDirectiveSourceFilename,
} from './source-documents.js';

describe('directive source management helpers', () => {
  it('enforces the shared safe PDF basename contract', () => {
    expect(validateDirectiveSourceFilename('policy.pdf')).toBe(true);
    expect(validateDirectiveSourceFilename('český název.v2.PDF')).toBe(true);
    expect(validateDirectiveSourceFilename('policy_backup.final.pdf')).toBe(true);
    expect(validateDirectiveSourceFilename('')).toBe(false);
    expect(validateDirectiveSourceFilename('policy.txt')).toBe(false);
    expect(validateDirectiveSourceFilename('policy/name.pdf')).toBe(false);
    expect(validateDirectiveSourceFilename('policy\\name.pdf')).toBe(false);
    expect(validateDirectiveSourceFilename('policy\u0000name.pdf')).toBe(false);
    expect(validateDirectiveSourceFilename('policy\nname.pdf')).toBe(false);
    expect(validateDirectiveSourceFilename('.')).toBe(false);
    expect(validateDirectiveSourceFilename('a'.repeat(252) + '.pdf')).toBe(false);
  });

  it('maps stable upload failures without suggesting overwrite', () => {
    expect(
      directiveSourceUploadError(
        409,
        'company-policy.pdf',
      ),
    ).toEqual({
      status: 'conflict',
      message: (
        '"company-policy.pdf" already exists and was not '
        + 'overwritten.'
      ),
    });
    expect(directiveSourceUploadError(413, 'source.pdf').status).toBe(
      'too_large',
    );
    expect(directiveSourceUploadError(400, 'source.pdf').status).toBe(
      'invalid',
    );
  });

  it('makes source deletion retention explicit', () => {
    expect(DIRECTIVE_SOURCE_RETENTION_NOTICE).toContain(
      'Previously ingested content',
    );
    expect(DIRECTIVE_SOURCE_RETENTION_NOTICE).toContain('retained');
  });
});

describe('directive source client', () => {
  const storage = new Map<string, string>();
  const fetchMock = vi.fn<
    (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  >();

  beforeEach(() => {
    storage.set('mockUserId', 'user-alice');
    vi.stubGlobal('window', {
      __APP_CONFIG__: { apiBaseUrl: '/api', authMode: 'mock' },
      location: { search: '' },
    });
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    });
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('lists metadata and deletes only the exact encoded filename', async () => {
    const { AGUIClient } = await import('./client.js');
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            items: [
              {
                filename: 'český název.v2.PDF',
                size_bytes: 100,
                last_modified: '2026-07-25T00:00:00Z',
              },
            ],
            next_cursor: null,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ deleted: true }), { status: 200 }),
      );
    const client = new AGUIClient();

    const page = await client.listDirectiveSources(null, 25);
    await client.deleteDirectiveSource(
      'český název.v2.PDF',
    );

    expect(page.items[0].filename).toBe('český název.v2.PDF');
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/directive-sources?limit=25',
      expect.objectContaining({
        headers: { 'X-Mock-User-ID': 'user-alice' },
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/directive-sources/%C4%8Desk%C3%BD%20n%C3%A1zev.v2.PDF?confirm=true',
      expect.objectContaining({
        method: 'DELETE',
        headers: { 'X-Mock-User-ID': 'user-alice' },
      }),
    );
  });

  it('streams the raw PDF through XHR and reports upload progress', async () => {
    class FakeXMLHttpRequest {
      static latest: FakeXMLHttpRequest | null = null;

      readonly headers = new Map<string, string>();
      readonly uploadListeners = new Map<
        string,
        (event: ProgressEvent) => void
      >();
      readonly listeners = new Map<string, () => void>();
      readonly upload = {
        addEventListener: (
          type: string,
          listener: (event: ProgressEvent) => void,
        ) => this.uploadListeners.set(type, listener),
      };
      status = 201;
      responseText = JSON.stringify({
        filename: 'český název.v2.PDF',
        size_bytes: 100,
        last_modified: '2026-07-25T00:00:00Z',
      });
      method = '';
      url = '';
      body: Document | XMLHttpRequestBodyInit | null = null;

      constructor() {
        FakeXMLHttpRequest.latest = this;
      }

      open(method: string, url: string): void {
        this.method = method;
        this.url = url;
      }

      setRequestHeader(name: string, value: string): void {
        this.headers.set(name, value);
      }

      addEventListener(type: string, listener: () => void): void {
        this.listeners.set(type, listener);
      }

      send(body: Document | XMLHttpRequestBodyInit | null): void {
        this.body = body;
        this.uploadListeners.get('progress')?.({
          lengthComputable: true,
          loaded: 50,
          total: 100,
        } as ProgressEvent);
        this.listeners.get('load')?.();
      }

      abort(): void {
        this.listeners.get('abort')?.();
      }
    }

    vi.stubGlobal('XMLHttpRequest', FakeXMLHttpRequest);
    const { AGUIClient } = await import('./client.js');
    const client = new AGUIClient();
    const file = Object.assign(
      new Blob(['%PDF-source'], { type: 'application/pdf' }),
      {
        name: 'český název.v2.PDF',
        lastModified: 0,
      },
    ) as File;
    const progress: number[] = [];

    const uploaded = await client.uploadDirectiveSource(
      file,
      (percent) => progress.push(percent),
    );
    const request = FakeXMLHttpRequest.latest;

    expect(uploaded.filename).toBe(file.name);
    expect(progress).toEqual([50]);
    expect(request?.method).toBe('POST');
    expect(request?.url).toBe(
      '/api/directive-sources/upload/%C4%8Desk%C3%BD%20n%C3%A1zev.v2.PDF',
    );
    expect(request?.headers.get('Content-Type')).toBe('application/pdf');
    expect(request?.headers.get('X-Mock-User-ID')).toBe('user-alice');
    expect(request?.body).toBe(file);
  });
});
