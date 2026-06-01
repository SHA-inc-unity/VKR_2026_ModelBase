'use client';
import { CheckCircle2, Loader2, XCircle } from 'lucide-react';
import { SmoothProgress } from '@/components/ui/smooth-progress';
import { cn } from '@/lib/utils';
import type { IngestStage } from '@/lib/types';

export function IngestProgress({ stages }: { stages: IngestStage[] }) {
  return (
    <div className="flex flex-col gap-2 pt-2">
      {stages.map(s => (
        <div key={s.id} className="flex flex-col gap-1">
          <div className="flex items-center gap-3">
            <div className="flex-shrink-0 w-3.5 h-3.5 flex items-center justify-center">
              {s.status === 'pending' && (
                <div className="w-3 h-3 rounded-full border-2 border-muted-foreground/30" />
              )}
              {s.status === 'running' && (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
              )}
              {s.status === 'done' && (
                <CheckCircle2 className="w-3.5 h-3.5 text-success" />
              )}
              {s.status === 'error' && (
                <XCircle className="w-3.5 h-3.5 text-destructive" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <span className={cn(
                'text-xs',
                s.status === 'pending' && 'text-muted-foreground',
                s.status === 'error' && 'text-destructive',
              )}>
                {s.label}
              </span>
              {s.detail && s.status !== 'pending' && (
                <div className="text-[10px] text-muted-foreground truncate">{s.detail}</div>
              )}
            </div>
            {s.status === 'running' && (
              <span className="text-[10px] text-muted-foreground tabular-nums flex-shrink-0">
                {s.progress}%
              </span>
            )}
          </div>
          {s.status === 'running' && (
            <SmoothProgress value={s.progress} running className="h-0.5 w-full ml-6" />
          )}
        </div>
      ))}
    </div>
  );
}
