import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { ACCENT_BORDER, type AccentColor } from './constants';

// ── Stat card ──
export function StatCard({
  label, value, loading, icon: Icon, accentColor,
}: {
  label: string;
  value: string;
  loading: boolean;
  icon: React.ComponentType<{ className?: string }>;
  accentColor?: AccentColor;
}) {
  return (
    <Card className={cn('border-l-4', accentColor && ACCENT_BORDER[accentColor])}>
      <CardHeader className="pb-2 pt-5 px-5">
        <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-2">
          <Icon className="w-3.5 h-3.5" />
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-5 pb-5">
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <div className="text-3xl font-bold tracking-tight">{value}</div>
        )}
      </CardContent>
    </Card>
  );
}
