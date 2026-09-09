import { safeMarkdownUrl } from '../../../components/content/markdown-policy.ts';

import type { SearchMetadata } from '../types.ts';

export function searchNotice(search: SearchMetadata): string | null {
  if (search.status === 'pending' || search.status === 'searching')
    return '웹에서 참고 자료를 찾고 있습니다…';
  if (search.status === 'completed') return null;
  if (search.status === 'disabled' && search.reason === 'mode_changed')
    return '로컬 전용 설정이 변경되어 웹검색을 중단했습니다. 최신 정보는 확인하지 못했습니다.';
  if (search.status === 'disabled' && search.reason === 'forced_local')
    return '로컬 전용 모드로 웹검색을 사용하지 않았습니다. 최신 정보는 확인하지 못했습니다.';
  if (search.status === 'disabled' && search.reason === 'search_off')
    return '웹검색을 끄고 로컬 지식으로 답합니다. 최신 정보는 확인하지 못했습니다.';
  if (search.reason === 'provider_unconfigured')
    return '검색 서비스가 설정되지 않아 로컬 지식으로 답합니다. 최신 정보는 확인하지 못했습니다.';
  if (search.status === 'unavailable')
    return '인터넷 검색에 연결하지 못해 로컬 지식으로 답합니다. 최신 정보는 확인하지 못했습니다.';
  if (search.status === 'failed')
    return '웹검색에 실패해 로컬 지식으로 답합니다. 최신 정보는 확인하지 못했습니다.';
  if (search.status === 'no_results')
    return '관련 검색 결과가 없어 로컬 지식으로 답합니다. 최신 정보는 확인하지 못했습니다.';
  if (search.status === 'cancelled')
    return '웹검색이 중단되었습니다. 최신 정보는 확인하지 못했습니다.';
  if (search.status === 'omitted')
    return '검색 자료를 이번 답변에 포함하지 못했습니다. 최신 정보는 확인하지 못했습니다.';
  return null;
}

function sourceUrl(value: string): string | undefined {
  const safe = safeMarkdownUrl(value);
  if (!safe) return undefined;
  try {
    const url = new URL(safe);
    return ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? safe
      : undefined;
  } catch {
    return undefined;
  }
}

export function SearchSources({ search }: { search?: SearchMetadata | null }) {
  if (!search) return null;
  const notice = searchNotice(search);
  if (!notice && search.sources.length === 0) return null;
  return (
    <section
      aria-label="웹검색 출처"
      className="mt-4 border-l border-border pl-4 text-xs leading-6 text-muted-foreground"
    >
      {notice && <output className="block">{notice}</output>}
      {search.sources.length > 0 && (
        <details>
          <summary className="cursor-pointer select-none font-medium text-foreground focus-visible:outline-2 focus-visible:outline-ring">
            참고한 검색 자료 ({search.sources.length})
          </summary>
          <ol className="mt-2 space-y-1">
            {search.sources.map((source) => {
              const url = sourceUrl(source.url);
              return (
                <li
                  key={`${source.number}:${source.url}`}
                  className="flex min-w-0 items-baseline gap-1"
                >
                  <span className="shrink-0 text-primary">
                    [{source.number}]
                  </span>
                  {url ? (
                    <a
                      href={url}
                      title={source.title || url}
                      target="_blank"
                      rel="noopener noreferrer"
                      referrerPolicy="no-referrer"
                      className="min-w-0 truncate text-primary underline underline-offset-2"
                    >
                      {source.title || url}
                    </a>
                  ) : (
                    <span className="min-w-0 truncate" title={source.title}>
                      {source.title || '출처 주소를 표시할 수 없습니다.'}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </details>
      )}
    </section>
  );
}
