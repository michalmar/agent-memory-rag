export type DirectiveSourceUploadStatus =
  | 'idle'
  | 'uploading'
  | 'conflict'
  | 'too_large'
  | 'invalid'
  | 'error';

export const DIRECTIVE_SOURCE_RETENTION_NOTICE =
  'Previously ingested content and citations will be retained.';

const DIRECTIVE_SOURCE_FILENAME =
  /^\d{8}-[^/\\]+-v\d+(?:\.\d+)?\.pdf$/i;

export function validateDirectiveSourceFilename(filename: string): boolean {
  return filename.length <= 255 && DIRECTIVE_SOURCE_FILENAME.test(filename);
}

export function directiveSourceUploadError(
  status: number,
  filename: string,
): { status: DirectiveSourceUploadStatus; message: string } {
  if (status === 409) {
    return {
      status: 'conflict',
      message: `"${filename}" already exists and was not overwritten.`,
    };
  }
  if (status === 413) {
    return {
      status: 'too_large',
      message: `"${filename}" exceeds the upload size limit.`,
    };
  }
  if (status === 400 || status === 422) {
    return {
      status: 'invalid',
      message: `"${filename}" is not a valid directive PDF.`,
    };
  }
  if (status === 403) {
    return {
      status: 'error',
      message: 'You are not authorized to manage directive sources.',
    };
  }
  return {
    status: 'error',
    message: 'The directive source could not be uploaded.',
  };
}
