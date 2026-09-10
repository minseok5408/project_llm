export type ContextStatus = {
  generation_id: string;
  phase: 'preparing' | 'ready' | 'finished';
  input_tokens: number | null;
  output_tokens: number | null;
  max_output_tokens: number;
  context_window: number;
  summary_through_sequence: number | null;
  compaction_status:
    | 'idle'
    | 'pending'
    | 'running'
    | 'completed'
    | 'failed'
    | 'cancelled';
};

export function validContextStatus(value: unknown): value is ContextStatus {
  if (!value || typeof value !== 'object') return false;
  const data = value as ContextStatus;
  const count = (value: unknown) =>
    Number.isSafeInteger(value) && Number(value) >= 0;
  return (
    typeof data.generation_id === 'string' &&
    ['preparing', 'ready', 'finished'].includes(data.phase) &&
    ['idle', 'pending', 'running', 'completed', 'failed', 'cancelled'].includes(
      data.compaction_status,
    ) &&
    (data.input_tokens === null || count(data.input_tokens)) &&
    (data.output_tokens === null || count(data.output_tokens)) &&
    count(data.max_output_tokens) &&
    data.max_output_tokens > 0 &&
    count(data.context_window) &&
    data.context_window > 0 &&
    (data.summary_through_sequence === null ||
      count(data.summary_through_sequence))
  );
}
