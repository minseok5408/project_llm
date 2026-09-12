'use client';

import { useRef } from 'react';
import { Paperclip } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function FilePicker({
  disabled,
  onUpload,
}: {
  disabled: boolean;
  onUpload: (file: File) => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={picker}
        type="file"
        accept=".pdf,.txt,.md,.csv,.json"
        className="hidden"
        aria-label="첨부할 파일 선택"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onUpload(file);
        }}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-9 shrink-0 rounded-full"
        aria-label="파일 첨부"
        title="PDF·TXT·Markdown·CSV·JSON (10MB 이하)"
        disabled={disabled}
        onClick={() => picker.current?.click()}
      >
        <Paperclip className="size-4" />
      </Button>
    </>
  );
}
