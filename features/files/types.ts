export type AttachedFile = {
  id: string;
  filename: string;
  version_id: string;
  status: 'uploaded' | 'scanning' | 'parsing' | 'indexing' | 'ready' | 'failed';
  byte_size: number;
  pages: number | null;
  chunks: number | null;
  attempts: number;
  error: string | null;
  can_delete: boolean;
  can_retry: boolean;
  dead_letter: boolean;
};
export type FilePage = {
  enabled: boolean;
  items: AttachedFile[];
  max_bytes: number;
  limit: number;
};
export type ConversationFileState = {
  files: AttachedFile[];
  filesEnabled: boolean;
  filesLoading: boolean;
  fileBusy: boolean;
  fileError: string | null;
};
export type FileSource = {
  available?: boolean;
  document_id: string;
  chunk_id: string;
  filename: string;
  page: number;
  chunk: number;
  start: number;
  end: number;
  number: number;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function validFileSources(value: unknown): value is FileSource[] {
  return (
    Array.isArray(value) &&
    value.length <= 4 &&
    value.every(
      (item) =>
        item &&
        uuid.test(item.document_id) &&
        uuid.test(item.chunk_id) &&
        typeof item.filename === 'string' &&
        item.filename.length <= 180 &&
        ['page', 'chunk', 'number'].every(
          (key) => Number.isSafeInteger(item[key]) && item[key] > 0,
        ) &&
        Number.isSafeInteger(item.start) &&
        item.start >= 0 &&
        Number.isSafeInteger(item.end) &&
        item.end > item.start,
    )
  );
}
