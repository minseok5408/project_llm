import type { Metadata } from 'next';
import { ThemeSync } from '@/features/preferences/components/theme-setting';
import { THEME_BOOTSTRAP_SCRIPT } from '@/features/preferences/state/theme';
import './globals.css';

export const metadata: Metadata = {
  title: 'Project LLM',
  description: 'Apple Silicon에서 실행되는 로컬 LLM 채팅',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP_SCRIPT }} />
      </head>
      <body>
        <ThemeSync />
        {children}
      </body>
    </html>
  );
}
