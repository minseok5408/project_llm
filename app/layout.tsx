import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'qwen-workbench — 로컬 터미널',
  description: 'Apple Silicon에서 실행하는 터미널형 로컬 Qwen 채팅 환경',
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
