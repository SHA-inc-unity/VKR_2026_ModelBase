'use client';

import { createContext, useCallback, useContext, useState } from 'react';

type ToastType = 'success' | 'error' | 'info';

interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
}

interface ToastContextValue {
  toast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextValue>({ toast: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const toast = useCallback((message: string, type: ToastType = 'info') => {
    const id = Math.random().toString(36).slice(2);
    setToasts(prev => [...prev, { id, type, message }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4000);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      {toasts.length > 0 && (
        <div
          className="fixed bottom-6 right-6 z-50 flex flex-col gap-2"
          style={{ minWidth: 300, maxWidth: 420 }}
        >
          {toasts.map(t => (
            <div
              key={t.id}
              className="flex overflow-hidden rounded-lg shadow-xl"
              style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
            >
              <div
                className="w-1 flex-shrink-0"
                style={{
                  background:
                    t.type === 'success' ? 'var(--success)' :
                    t.type === 'error'   ? 'var(--error)' :
                    'var(--accent)',
                }}
              />
              <div className="flex items-start gap-2 px-3 py-3">
                <span
                  className="text-sm font-bold mt-px"
                  style={{
                    color:
                      t.type === 'success' ? 'var(--success)' :
                      t.type === 'error'   ? 'var(--error)' :
                      'var(--accent)',
                  }}
                >
                  {t.type === 'success' ? '✓' : t.type === 'error' ? '✕' : 'ℹ'}
                </span>
                <span className="text-sm leading-5" style={{ color: 'var(--text)' }}>{t.message}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}
