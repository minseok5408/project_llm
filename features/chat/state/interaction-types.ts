export type ProgressItem = {
  id: string;
  name: string;
  status:
    | 'pending'
    | 'running'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'skipped';
};
export type QuestionCard = {
  questions: { question: string; options: string[] }[];
  answers?: string[];
  response_generation_id?: string;
};

export function validProgress(value: unknown): value is ProgressItem[] {
  return (
    Array.isArray(value) &&
    value.length <= 16 &&
    value.every(
      (item) =>
        item &&
        typeof item.id === 'string' &&
        typeof item.name === 'string' &&
        [
          'pending',
          'running',
          'completed',
          'failed',
          'cancelled',
          'skipped',
        ].includes(item.status),
    ) &&
    new Set(value.map((item) => item.id)).size === value.length
  );
}
