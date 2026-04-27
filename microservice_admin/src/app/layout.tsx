import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import { Sidebar } from '@/components/Sidebar';
import { ToastProvider } from '@/components/Toast';
import { LocaleProvider } from '@/lib/i18nContext';

const inter = Inter({ subsets: ['latin'], display: 'swap' });

export const metadata: Metadata = {
  title: 'ModelLine Admin',
  description: 'ModelLine trading model management panel',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.className}>
      <body>
        <LocaleProvider>
          <ToastProvider>
            <div className="flex h-screen overflow-hidden flex-col md:flex-row">
              <Sidebar />
              <main className="flex-1 overflow-auto p-3 sm:p-4 md:p-5 lg:p-6 pb-14 md:pb-5 lg:pb-6">
                <div className="max-w-full md:max-w-[1920px] mx-auto w-full">{children}</div>
              </main>
            </div>
          </ToastProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
