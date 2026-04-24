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

const NAV = [
  { href: '/',         label: 'Dashboard', icon: LayoutDashboard },
  { href: '/download', label: 'Dataset',   icon: Database },
  { href: '/train',    label: 'Train',     icon: BrainCircuit },
  { href: '/compare',  label: 'Compare',   icon: GitCompare },
  { href: '/anomaly',  label: 'Anomaly',   icon: ShieldAlert },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const [kafkaOk,   setKafkaOk]   = useState<boolean | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  // Restore sidebar state from localStorage (client-only)
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

  return (
    <aside className={cn(
      'flex flex-col flex-shrink-0 bg-card border-r border-border transition-all duration-200',
      collapsed ? 'w-14' : 'w-56',
    )}>
      {/* Brand */}
      <div className="flex items-center gap-3 px-3 h-14 border-b border-border">
        <div className="flex items-center justify-center w-7 h-7 rounded-md bg-primary flex-shrink-0">
          <Zap className="w-4 h-4 text-primary-foreground" />
        </div>
        {!collapsed && (
          <span className="font-bold text-base tracking-tight truncate">ModelLine</span>
        )}
        {!collapsed && (
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
        {/* Collapse toggle */}
        <button
          onClick={toggleCollapsed}
          className={cn(
            'flex items-center justify-center w-6 h-6 rounded hover:bg-accent transition-colors flex-shrink-0 text-muted-foreground',
            collapsed ? 'mx-auto' : 'ml-1',
          )}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-0.5 p-2 pt-3 flex-1">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={cn(
                'flex items-center rounded-md text-sm font-medium transition-colors relative',
                collapsed ? 'justify-center px-2 py-2' : 'gap-3 px-3 py-2 border-l-2 pl-[10px]',
                active
                  ? collapsed
                    ? 'bg-primary/10 text-foreground'
                    : 'bg-primary/10 text-foreground border-primary'
                  : collapsed
                    ? 'text-muted-foreground hover:bg-accent hover:text-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground border-transparent',
              )}
            >
              <Icon className={cn('w-4 h-4 flex-shrink-0', active ? 'text-primary' : '')} />
              {!collapsed && label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className={cn('p-3', collapsed ? 'flex justify-center' : '')}>
        {!collapsed && <Separator className="mb-3" />}
        {collapsed ? (
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
        ) : (
          <>
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
            <p className="text-xs text-muted-foreground mt-2 opacity-50">v1.0.0</p>
          </>
        )}
      </div>
    </aside>
  );
}


