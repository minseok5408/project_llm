import type { Metadata } from 'next';
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
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
