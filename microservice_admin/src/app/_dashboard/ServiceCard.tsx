import type { ServiceHealth } from '@/lib/types';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

// ── Service card (horizontal, bento row) ──
export function ServiceCard({
  name, stack, health, loading, lastSuccess,
}: {
  name: string;
  stack: string[];
  health: ServiceHealth | null;
  loading: boolean;
  lastSuccess: Date | null;
}) {
  const ok = health?.status === 'ok';
  const hasData = health !== null;

  return (
    <Card className="flex flex-col xs:flex-row xs:items-center gap-3 xs:gap-5 px-4 xs:px-6 py-4">
      {/* Left - name + stack */}
      <div className="flex-1 min-w-0">
        <div className="font-semibold text-sm truncate">{name}</div>
        <div className="flex gap-1.5 mt-1.5 flex-wrap">
          {stack.map(s => (
            <Badge key={s} variant="secondary" className="text-[10px] px-1.5 py-0">{s}</Badge>
          ))}
        </div>
      </div>

      {/* Center - status dot + label */}
      <div className="flex items-center gap-2.5 flex-shrink-0 xs:min-w-[110px]">
        {!hasData && loading ? (
          <>
            <Skeleton className="w-2.5 h-2.5 rounded-full" />
            <Skeleton className="h-4 w-12" />
          </>
        ) : (
          <>
            <span
              className={cn(
                'w-2.5 h-2.5 rounded-full flex-shrink-0',
                ok ? 'bg-success status-dot-ok' : 'bg-destructive',
              )}
            />
            <span className={cn('text-sm font-medium', ok ? 'text-success' : 'text-destructive')}>
              {ok ? 'Online' : 'Error'}
            </span>
          </>
        )}
      </div>

      {/* Right - last seen */}
      <div className="flex-shrink-0 text-left xs:text-right xs:min-w-[140px]">
        {!hasData && loading ? (
          <Skeleton className="h-4 w-28 ml-auto" />
        ) : ok && lastSuccess ? (
          <div>
            <div className="text-xs text-muted-foreground">Last seen</div>
            <div className="text-xs font-medium mt-0.5">{lastSuccess.toLocaleTimeString()}</div>
          </div>
        ) : health?.error ? (
          <span className="text-xs text-destructive truncate max-w-[140px]">{health.error}</span>
        ) : (
          <span className="text-xs text-muted-foreground">–</span>
        )}
      </div>
    </Card>
  );
}
