'use client';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { cacheRead, cacheWrite } from '@/lib/cacheClient';
import { RefreshCw, Table2, Rows, CalendarClock, GitMerge } from 'lucide-react';
import { kafkaCall } from '@/lib/kafkaClient';
import { fetchInfraHealth } from '@/lib/healthClient';
import { Topics } from '@/lib/topics';
import type { ServiceHealth, TableCoverage, ModelInfo, InfraServiceHealth } from '@/lib/types';
import { useEvents } from '@/hooks/useEvents';
import { getCoveragePct } from '@/lib/constants';
import { useLocale } from '@/lib/i18nContext';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cn } from '@/lib/utils';
import type { BarDatum } from '@/components/charts/CoverageBar';

// Dynamic import - avoids Recharts SSR errors in Next.js
const CoverageBar = dynamic(
  () => import('@/components/charts/CoverageBar').then(m => m.CoverageBar),
  { ssr: false, loading: () => <Skeleton className="h-[220px] w-full" /> },
);

const HEALTH_TIMEOUT   = 5_000;
const TABLES_TIMEOUT   = 8_000;
const COVERAGE_TIMEOUT = 5_000;

const DASHBOARD_CACHE_KEY = 'modelline:dashboard:v1';
const DASHBOARD_CACHE_TTL = 3600; // 1 hour

interface DashboardCache {
  tables: string[];
  coverage: Record<string, TableCoverage>;
  modelCount: number | null;
}

// в”Ђв”Ђ Sub-components в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
type AccentColor = 'primary' | 'success' | 'warning' | 'destructive';

const ACCENT_BORDER: Record<AccentColor, string> = {
  primary:     'border-l-primary',
  success:     'border-l-success',
  warning:     'border-l-warning',
  destructive: 'border-l-destructive',
};

// в”Ђв”Ђ Service card (horizontal, bento row) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
function ServiceCard({
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

// в”Ђв”Ђ Stat card в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
function StatCard({
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

// в”Ђв”Ђ Page в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
export default function DashboardPage() {
  const [dataHealth,      setDataHealth]      = useState<ServiceHealth | null>(null);
  const [tables,          setTables]          = useState<string[]>([]);
  const [coverage,        setCoverage]        = useState<Record<string, TableCoverage>>({});
  const [modelCount,      setModelCount]      = useState<number | null>(null);

  const [analiticHealth, setAnaliticHealth] = useState<ServiceHealth | null>(null);
  const [redpandaInfra,  setRedpandaInfra]  = useState<InfraServiceHealth | null>(null);
  const [minioInfra,     setMinioInfra]     = useState<InfraServiceHealth | null>(null);
  const [accountInfra,   setAccountInfra]   = useState<InfraServiceHealth | null>(null);
  const [gatewayInfra,   setGatewayInfra]   = useState<InfraServiceHealth | null>(null);

  const [dataLoading,     setDataLoading]     = useState(true);
  const [analiticLoading, setAnaliticLoading] = useState(true);
  const [tablesLoading,   setTablesLoading]   = useState(true);
  const [modelsLoading,   setModelsLoading]   = useState(true);
  const [infraLoading,    setInfraLoading]    = useState(true);

  const [lastRefresh,          setLastRefresh]          = useState<Date | null>(null);
  const [lastDataSuccess,      setLastDataSuccess]      = useState<Date | null>(null);
  const [lastAnalyticsSuccess, setLastAnalyticsSuccess] = useState<Date | null>(null);
  const [lastRedpandaSuccess,  setLastRedpandaSuccess]  = useState<Date | null>(null);
  const [lastMinioSuccess,     setLastMinioSuccess]     = useState<Date | null>(null);
  const [lastAccountSuccess,   setLastAccountSuccess]   = useState<Date | null>(null);
  const [lastGatewaySuccess,   setLastGatewaySuccess]   = useState<Date | null>(null);

  const pendingSaveRef = useRef(false);

  const totalRows = useMemo(
    () => tables.reduce((s, t) => s + (coverage[t]?.rows ?? 0), 0),
    [tables, coverage],
  );
  const lastIngestion = useMemo(
    () => tables.reduce((m, t) => {
      const ts = coverage[t]?.max_ts_ms;
      return ts ? Math.max(m, ts) : m;
    }, 0),
    [tables, coverage],
  );
  const coverageLoaded = tables.filter(t => coverage[t] !== undefined).length;
  const statsLoading   = tablesLoading || (tables.length > 0 && coverageLoaded === 0);
  const coverageChartData: BarDatum[] = useMemo(
    () => tables.map(t => ({ name: t, pct: getCoveragePct(t, coverage[t]) ?? 0 })),
    [tables, coverage],
  );

  const refresh = useCallback(() => {
    setDataLoading(true);
    setAnaliticLoading(true);
    setTablesLoading(true);
    setModelsLoading(true);

    kafkaCall<ServiceHealth>(Topics.CMD_DATA_HEALTH, {}, HEALTH_TIMEOUT)
      .then(h => { setDataHealth(h); if (h.status === 'ok') setLastDataSuccess(new Date()); })
      .catch(() => setDataHealth({ status: 'error', error: 'unreachable' }))
      .finally(() => setDataLoading(false));

    kafkaCall<ServiceHealth>(Topics.CMD_ANALYTICS_HEALTH, {}, HEALTH_TIMEOUT)
      .then(h => { setAnaliticHealth(h); if (h.status === 'ok') setLastAnalyticsSuccess(new Date()); })
      .catch(() => setAnaliticHealth({ status: 'error', error: 'unreachable' }))
      .finally(() => setAnaliticLoading(false));

    kafkaCall<{
      tables: Array<string | {
        table_name: string;
        rows?: number;
        coverage_pct?: number;
        date_from?: string | null;
        date_to?: string | null;
      }>;
    }>(Topics.CMD_DATA_DATASET_LIST_TABLES, {}, TABLES_TIMEOUT)
      .then(r => {
        const raw = r.tables ?? [];
        const names: string[] = raw.map(x => typeof x === 'string' ? x : x.table_name);
        setTables(names);
        pendingSaveRef.current = true;

        // Happy path: backend already returned enriched fields for each
        // table (rows + min/max ts derivable from date_from/date_to). One
        // round-trip total.
        const initialCoverage: Record<string, TableCoverage> = {};
        for (const t of raw) {
          if (typeof t === 'string') continue;
          if (t.rows == null) continue;
          initialCoverage[t.table_name] = {
            table:      t.table_name,
            exists:     true,
            rows:       t.rows ?? 0,
            // We don't carry ts back from the enriched response (the backend
            // formats them as YYYY-MM-DD); reconstruct from the date strings
            // when present so charts that key off min/max_ts_ms still render.
            min_ts_ms:  t.date_from ? Date.parse(`${t.date_from}T00:00:00Z`) : null,
            max_ts_ms:  t.date_to   ? Date.parse(`${t.date_to}T23:59:59Z`)   : null,
            status:     'ok',
          };
        }
        if (Object.keys(initialCoverage).length > 0) {
          setCoverage(prev => ({ ...prev, ...initialCoverage }));
        }

        // Transitional fallback: only fetch per-table coverage for legacy
        // string entries (older DataService that didn't enrich the list).
        for (const t of raw) {
          if (typeof t !== 'string') continue;
          kafkaCall<TableCoverage>(Topics.CMD_DATA_DATASET_COVERAGE, { table: t }, COVERAGE_TIMEOUT)
            .then(cv => setCoverage(prev => ({ ...prev, [t]: cv })))
            .catch(() => {});
        }
      })
      .catch(() => {})
      .finally(() => setTablesLoading(false));

    kafkaCall<{ models: ModelInfo[] }>(Topics.CMD_ANALYTICS_MODEL_LIST, {}, TABLES_TIMEOUT)
      .then(r => setModelCount(r.models?.length ?? 0))
      .catch(() => setModelCount(null))
      .finally(() => setModelsLoading(false));

    setInfraLoading(true);
    fetchInfraHealth()
      .then(infra => {
        const now = new Date();
        setRedpandaInfra(infra.redpanda);
        setMinioInfra(infra.minio);
        setAccountInfra(infra.account);
        setGatewayInfra(infra.gateway);
        if (infra.redpanda.status === 'online') setLastRedpandaSuccess(now);
        if (infra.minio.status    === 'online') setLastMinioSuccess(now);
        if (infra.account.status  === 'online') setLastAccountSuccess(now);
        if (infra.gateway.status  === 'online') setLastGatewaySuccess(now);
      })
      .catch(() => {
        const err: InfraServiceHealth = { status: 'offline', error: 'unreachable' };
        setRedpandaInfra(err);
        setMinioInfra(err);
        setAccountInfra(err);
        setGatewayInfra(err);
      })
      .finally(() => setInfraLoading(false));

    setLastRefresh(new Date());
  }, []);

  // On mount: restore from cache first, then refresh in background
  useEffect(() => {
    let cancelled = false;
    async function init() {
      const cached = await cacheRead<DashboardCache>(DASHBOARD_CACHE_KEY);
      if (!cancelled && cached) {
        setTables(cached.tables);
        setCoverage(cached.coverage);
        setModelCount(cached.modelCount);
        setTablesLoading(false);
        setModelsLoading(false);
      }
      if (!cancelled) refresh();
    }
    void init();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Save to cache after a full refresh completes (tables + all coverage loaded)
  useEffect(() => {
    if (!pendingSaveRef.current) return;
    if (tables.length === 0 || tablesLoading) return;
    const loaded = tables.filter(t => coverage[t] !== undefined).length;
    if (loaded < tables.length) return;
    pendingSaveRef.current = false;
    void cacheWrite(DASHBOARD_CACHE_KEY, { tables, coverage, modelCount }, DASHBOARD_CACHE_TTL);
  }, [tables, coverage, tablesLoading, modelCount]);

  useEvents({
    EVT_ANALYTICS_MODEL_READY: () => {
      setModelsLoading(true);
      kafkaCall<{ models: ModelInfo[] }>(Topics.CMD_ANALYTICS_MODEL_LIST, {}, TABLES_TIMEOUT)
        .then(r => setModelCount(r.models?.length ?? 0))
        .catch(() => setModelCount(null))
        .finally(() => setModelsLoading(false));
    },
  });

  const anyLoading = dataLoading || analiticLoading || tablesLoading || modelsLoading || infraLoading;
  const { t } = useLocale();

  const infraToHealth = (h: InfraServiceHealth | null): ServiceHealth | null => {
    if (!h) return null;
    return h.status === 'online'
      ? { status: 'ok' }
      : { status: 'error', error: h.error };
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6 w-full">

      {/* в”Ђв”Ђ Header в”Ђв”Ђ */}
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">{t('dashboard.title')}</h1>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-muted-foreground hidden sm:block">
              {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={refresh}
            disabled={anyLoading}
            className="gap-2"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', anyLoading && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
        </div>
      </header>

      {/* в”Ђв”Ђ Row 1: Stat cards в”Ђв”Ђ */}
      <section className="grid grid-cols-1 xs:grid-cols-2 xl:grid-cols-4 gap-2 sm:gap-3">
        <StatCard
          label={t('dashboard.totalTables')}
          value={String(tables.length)}
          loading={tablesLoading}
          icon={Table2}
          accentColor="primary"
        />
        <StatCard
          label={t('dashboard.totalRows')}
          value={totalRows > 0 ? totalRows.toLocaleString() : '–'}
          loading={statsLoading}
          icon={Rows}
          accentColor="success"
        />
        <StatCard
          label={t('dashboard.lastIngestion')}
          value={lastIngestion > 0 ? new Date(lastIngestion).toISOString().slice(0, 10) : '–'}
          loading={statsLoading}
          icon={CalendarClock}
          accentColor="warning"
        />
        <StatCard
          label={t('dashboard.modelsTrained')}
          value={modelCount !== null ? String(modelCount) : '–'}
          loading={modelsLoading}
          icon={GitMerge}
          accentColor="destructive"
        />
      </section>

      {/* в”Ђв”Ђ Row 2: Services (left) + Coverage chart (right) в”Ђв”Ђ */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* Service health cards stacked */}
        <div className="flex flex-col gap-3">
          <ServiceCard
            name="microservice_data"
            stack={['.NET 8', 'PostgreSQL', 'Kafka']}
            health={dataHealth}
            loading={dataLoading}
            lastSuccess={lastDataSuccess}
          />
          <ServiceCard
            name="microservice_analitic"
            stack={['Python', 'FastAPI', 'CatBoost']}
            health={analiticHealth}
            loading={analiticLoading}
            lastSuccess={lastAnalyticsSuccess}
          />
          <ServiceCard
            name="Redpanda"
            stack={['Kafka', 'Streaming']}
            health={infraToHealth(redpandaInfra)}
            loading={infraLoading}
            lastSuccess={lastRedpandaSuccess}
          />
          <ServiceCard
            name="MinIO"
            stack={['S3', 'Storage']}
            health={infraToHealth(minioInfra)}
            loading={infraLoading}
            lastSuccess={lastMinioSuccess}
          />
          <ServiceCard
            name="microservice_account"
            stack={['.NET 8', 'PostgreSQL', 'JWT']}
            health={infraToHealth(accountInfra)}
            loading={infraLoading}
            lastSuccess={lastAccountSuccess}
          />
          <ServiceCard
            name="microservice_gateway"
            stack={['.NET 8', 'JWT', 'Kafka']}
            health={infraToHealth(gatewayInfra)}
            loading={infraLoading}
            lastSuccess={lastGatewaySuccess}
          />
        </div>

        {/* Coverage bar chart */}
        <Card>
          <CardHeader className="pb-0 px-6 pt-5">
            <CardTitle className="text-sm font-semibold">{t('dashboard.coverage')}</CardTitle>
          </CardHeader>
          <CardContent className="pt-4 px-4 pb-4">
            {tablesLoading || (tables.length > 0 && coverageLoaded === 0) ? (
              <Skeleton className="h-[220px] w-full" />
            ) : tables.length === 0 ? (
              <div className="flex items-center justify-center h-[220px] text-sm text-muted-foreground">
                No tables yet
              </div>
            ) : (
              <CoverageBar data={coverageChartData} height={220} />
            )}
          </CardContent>
        </Card>
      </section>

      {/* в”Ђв”Ђ Row 3: Dataset tables в”Ђв”Ђ */}
      <section>
        <Card>
          <CardHeader className="pb-0 px-6">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold">{t('dashboard.tables')}</CardTitle>
              {!tablesLoading && coverageLoaded < tables.length && tables.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  Loading coverage {coverageLoaded}/{tables.length}
                </span>
              )}
            </div>
          </CardHeader>
          <Separator className="mt-4" />
          <CardContent className="p-0">
            {tablesLoading ? (
              <div className="p-6 space-y-3">
                {[...Array(4)].map((_, i) => (
                  <Skeleton key={i} className="h-9 w-full" />
                ))}
              </div>
            ) : tables.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 gap-2 text-center">
                <Table2 className="w-8 h-8 text-muted-foreground/30" />
                <p className="text-sm font-medium">No dataset tables yet</p>
                <p className="text-xs text-muted-foreground">
                  Ingest data from the Dataset page to create tables
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead className="text-right">Rows</TableHead>
                    <TableHead>Coverage</TableHead>
                    <TableHead>From</TableHead>
                    <TableHead>To</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tables.map(t => {
                    const cv  = coverage[t];
                    const pct = cv ? getCoveragePct(t, cv) : null;
                    return (
                      <TableRow key={t}>
                        <TableCell className="font-mono text-xs">{t}</TableCell>
                        <TableCell className="text-right text-xs">
                          {cv ? cv.rows.toLocaleString() : <Skeleton className="h-4 w-16 ml-auto" />}
                        </TableCell>
                        <TableCell style={{ minWidth: 140 }}>
                          {cv === undefined ? (
                            <Skeleton className="h-4 w-full" />
                          ) : pct !== null ? (
                            <div className="flex items-center gap-2">
                              <Progress
                                value={pct}
                                className={cn(
                                  'h-1.5 flex-1',
                                  pct > 80 ? '[&>div]:bg-success' :
                                  pct > 40 ? '[&>div]:bg-warning' : '[&>div]:bg-destructive',
                                )}
                              />
                              <span className="text-xs tabular-nums w-10 text-right">{pct.toFixed(1)}%</span>
                            </div>
                          ) : (
                            <span className="text-xs text-muted-foreground">–</span>
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {cv
                            ? (cv.min_ts_ms ? new Date(cv.min_ts_ms).toISOString().slice(0, 10) : '–')
                            : <Skeleton className="h-4 w-20" />}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {cv
                            ? (cv.max_ts_ms ? new Date(cv.max_ts_ms).toISOString().slice(0, 10) : '–')
                            : <Skeleton className="h-4 w-20" />}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
