export type SearchSource = {
  number: number;
  title: string;
  url: string;
  snippet: string;
  retrieved_at: string;
};
export type SearchMetadata = {
  status:
    | 'pending'
    | 'searching'
    | 'completed'
    | 'disabled'
    | 'unavailable'
    | 'failed'
    | 'no_results'
    | 'cancelled'
    | 'omitted';
  reason: string | null;
  provider: string;
  sources: SearchSource[];
};
