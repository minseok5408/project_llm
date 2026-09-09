export type TokenBalance = {
  unlimited: boolean;
  token_limit: number | null;
  used_tokens: number;
  reserved_tokens: number;
  remaining_tokens: number | null;
  starts_at: string | null;
  ends_at: string | null;
  budget_source?: 'free_monthly' | 'plan' | null;
  plan_name?: string | null;
};
