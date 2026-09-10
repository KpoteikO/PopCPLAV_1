import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
export const metadata: Metadata = {
  title: 'PopCat — локальный AI под контролем',
  description: 'Панель подключения и диагностики Ollama, Nginx, Python API и React. Локальный AI без лишней сложности.',
};
export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="ru"><body>{children}</body></html>;
}
