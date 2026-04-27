'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Database,
  BrainCircuit,
  GitCompare,
  ShieldAlert,
  Zap,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Separator } from '@/components/ui/separator';
import { kafkaCall } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';
import { useLocale } from '@/lib/i18nContext';

const NAV_KEYS = [
  { href: '/',         key: 'nav.dashboard' as const, icon: LayoutDashboard },
  { href: '/download', key: 'nav.dataset'   as const, icon: Database },
  { href: '/train',    key: 'nav.train'     as const, icon: BrainCircuit },
  { href: '/compare',  key: 'nav.compare'   as const, icon: GitCompare },
  { href: '/anomaly',  key: 'nav.anomaly'   as const, icon: ShieldAlert },
] as const;

type Mode = 'expanded-collapsible' | 'icon-only' | 'bottom-nav';

function detectMode(): Mode {
  if (typeof window === 'undefined') return 'expanded-collapsible';
  const w = window.innerWidth;
  if (w < 768) return 'bottom-nav';
  if (w < 1024) return 'icon-only';
  return 'expanded-collapsible';
}

export function Sidebar() {
  const pathname = usePathname();
  const { locale, setLocale, t } = useLocale();
  const [kafkaOk,   setKafkaOk]   = useState<boolean | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mode, setMode] = useState<Mode>('expanded-collapsible');

  // Reactive mode detection based on viewport width
  useEffect(() => {
    const update = () => setMode(detectMode());
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  // Restore collapsed state from localStorage — only relevant in mode A
  useEffect(() => {
    const stored = localStorage.getItem('modelline:sidebar:collapsed');
    if (stored !== null) setCollapsed(stored === 'true');
  }, []);

  const toggleCollapsed = () => {
    setCollapsed(prev => {
      const next = !prev;
      localStorage.setItem('modelline:sidebar:collapsed', String(next));
      return next;
    });
  };

  useEffect(() => {
    const check = async () => {
      try {
        await kafkaCall(Topics.CMD_DATA_DB_PING, undefined, 2_000);
        setKafkaOk(true);
      } catch {
        setKafkaOk(false);
      }
    };
    check();
    const id = setInterval(check, 30_000);
    return () => clearInterval(id);
  }, []);

  // ── Mode C: bottom-nav (< md) ──────────────────────────────────────
  if (mode === 'bottom-nav') {
    return (
      <aside className="order-last flex flex-row w-full h-14 bg-card border-t border-border flex-shrink-0">
        <nav className="flex flex-row items-stretch justify-around w-full">
          {NAV_KEYS.map(({ href, key, icon: Icon }) => {
            const label = t(key);
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                title={label}
                aria-label={label}
                className={cn(
                  'flex flex-1 flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors',
                  active
                    ? 'text-foreground bg-primary/10'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                )}
              >
                <Icon className={cn('w-5 h-5', active ? 'text-primary' : '')} />
              </Link>
            );
          })}
        </nav>
      </aside>
    );
  }

  // ── Mode A (expanded/collapsible) or Mode B (icon-only) ────────────
  const isIconOnly = mode === 'icon-only';
  const effectiveCollapsed = isIconOnly ? true : collapsed;

  return (
    <aside className={cn(
      'flex flex-col flex-shrink-0 bg-card border-r border-border transition-all duration-200',
      effectiveCollapsed ? 'w-14' : 'w-56',
    )}>
      {/* Brand */}
      <div className="flex items-center gap-3 px-3 h-14 border-b border-border">
        <div className="flex items-center justify-center w-7 h-7 rounded-md bg-primary flex-shrink-0">
          <Zap className="w-4 h-4 text-primary-foreground" />
        </div>
        {!effectiveCollapsed && (
          <span className="font-bold text-base tracking-tight truncate">ModelLine</span>
        )}
        {!effectiveCollapsed && (
          /* Kafka status pulse — only when expanded */
          <span
            className={cn(
              'ml-auto w-2 h-2 rounded-full flex-shrink-0',
              kafkaOk === true  ? 'bg-success status-dot-ok' :
              kafkaOk === false ? 'bg-destructive'           : 'bg-muted-foreground',
            )}
            title={
              kafkaOk === true  ? 'Kafka connected' :
              kafkaOk === false ? 'Kafka error'     : 'Checking...'
            }
          />
        )}
        {/* Collapse toggle — hidden in icon-only mode */}
        {!isIconOnly && (
          <button
            onClick={toggleCollapsed}
            className={cn(
              'flex items-center justify-center w-6 h-6 rounded hover:bg-accent transition-colors flex-shrink-0 text-muted-foreground',
              effectiveCollapsed ? 'mx-auto' : 'ml-1',
            )}
            title={effectiveCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {effectiveCollapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-0.5 p-2 pt-3 flex-1">
        {NAV_KEYS.map(({ href, key, icon: Icon }) => {
          const label = t(key);
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              title={effectiveCollapsed ? label : undefined}
              className={cn(
                'flex items-center rounded-md text-sm font-medium transition-colors relative',
                effectiveCollapsed ? 'justify-center px-2 py-2' : 'gap-3 px-3 py-2 border-l-2 pl-[10px]',
                active
                  ? effectiveCollapsed
                    ? 'bg-primary/10 text-foreground'
                    : 'bg-primary/10 text-foreground border-primary'
                  : effectiveCollapsed
                    ? 'text-muted-foreground hover:bg-accent hover:text-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground border-transparent',
              )}
            >
              <Icon className={cn('w-4 h-4 flex-shrink-0', active ? 'text-primary' : '')} />
              {!effectiveCollapsed && label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className={cn('p-3', effectiveCollapsed ? 'flex flex-col items-center gap-2' : '')}>
        {!effectiveCollapsed && <Separator className="mb-3" />}
        {effectiveCollapsed ? (
          <>
            {/* Collapsed: language toggle as two-char button */}
            <button
              onClick={() => setLocale(locale === 'en' ? 'ru' : 'en')}
              className="text-[10px] font-bold text-muted-foreground hover:text-foreground transition-colors leading-none"
              title={locale === 'en' ? 'Switch to Russian' : 'Switch to English'}
            >
              {locale === 'en' ? 'RU' : 'EN'}
            </button>
            <div
              className={cn(
                'w-2 h-2 rounded-full',
                kafkaOk === true  ? 'bg-success'    :
                kafkaOk === false ? 'bg-destructive' : 'bg-muted-foreground',
              )}
              title={
                kafkaOk === true  ? 'Kafka connected' :
                kafkaOk === false ? 'Kafka error'     : 'Checking...'
              }
            />
          </>
        ) : (
          <>
            <div className="flex items-center justify-between gap-2 mb-2">
              {/* Language toggle */}
              <div className="flex gap-0.5 p-0.5 rounded bg-muted">
                {(['en', 'ru'] as const).map(l => (
                  <button
                    key={l}
                    onClick={() => setLocale(l)}
                    className={cn(
                      'px-2 py-0.5 text-xs font-semibold rounded transition-colors',
                      locale === l
                        ? 'bg-card text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {l.toUpperCase()}
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground opacity-50">v1.0.0</p>
            </div>
            <div className="flex items-center gap-2">
              <div className={cn(
                'w-1.5 h-1.5 rounded-full flex-shrink-0',
                kafkaOk === true  ? 'bg-success'          :
                kafkaOk === false ? 'bg-destructive'       : 'bg-muted-foreground',
              )} />
              <span className="text-xs text-muted-foreground">
                {kafkaOk === true ? 'Kafka connected' : kafkaOk === false ? 'Kafka error' : 'Checking...'}
              </span>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
