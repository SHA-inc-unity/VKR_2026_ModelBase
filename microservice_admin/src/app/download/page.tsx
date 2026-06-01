'use client';
import dynamic from 'next/dynamic';
import { useEffect, useRef, useState } from 'react';
import { cacheRead, cacheWrite } from '@/lib/cacheClient';
import { CheckCircle2, Database, Download, DownloadCloud, Loader2, RefreshCw, ShieldCheck, Trash2, Wrench, XCircle } from 'lucide-react';
import { kafkaCall, newCorrelationId } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';
import { useToast } from '@/components/Toast';
import { useEvents } from '@/hooks/useEvents';
import { refreshActiveJobs, refreshJobsByIds, seedQueuedJob, useDatasetJobs } from '@/hooks/useDatasetJobs';
import { useDatasetJobsFeed } from '@/hooks/useDatasetJobsFeed';
import {
  TIMEFRAMES,
  TIMEFRAMES_ALL,
  TF_STEP_MS,
  makeTableName,
  getCoveragePct,
  formatDateFromMs,
} from '@/lib/constants';
import { useSymbols } from '@/hooks/useCurrencyPairs';
import type {
  TableCoverage,
  IngestStage,
  RepairStage,
  RepairStageId,
  QualityReport,
} from '@/lib/types';
import { useHistory } from '@/hooks/useHistory';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cn } from '@/lib/utils';
import type { BarDatum } from '@/components/charts/CoverageBar';
import { IngestProgress } from './_components/IngestProgress';
import { AllIngestProgress } from './_components/AllIngestProgress';
import {
  PARAMS_KEY,
  PARAMS_TTL,
  CACHE_TABLES_KEY,
  CACHE_TABLES_TTL,
  CACHE_COVERAGE_TTL,
  ALL_INGEST_ERROR_RETENTION_MS,
  ACTIVE_EXCHANGES,
  EXCHANGES,
  coverageCacheKey,
  allCoverageCacheKey,
  todayStr,
  daysAgoStr,
  shortenMessage,
  loadParams,
  formatRows,
  INITIAL_STAGES,
  INITIAL_REPAIR_STAGES_OHLCV,
  INITIAL_REPAIR_STAGES_RECOMPUTE,
  parseTableName,
  buildIngestScopeKey,
  parseIngestScopeKey,
  mapJobToStages,
  formatIngestSuccessToast,
  formatErrorHint,
  type DatasetExchange,
  type DatasetPageParams,
  type DataTableInfo,
  type CoverageResult,
  type AllCoverageItem,
  type TfStatus,
  type TfMeta,
} from './_lib/datasetHelpers';

// Dynamic import — avoids Recharts SSR errors
const CoverageBar = dynamic(
  () => import('@/components/charts/CoverageBar').then(m => m.CoverageBar),
  { ssr: false, loading: () => <Skeleton className="h-[100px] w-full" /> },
);

export default function DatasetPage() {
  const { toast } = useToast();
  const { symbols, symbolsAll } = useSymbols();
  const { history, addEntry } = useHistory();

  const saved = useRef(loadParams());
  const [symbol,    setSymbol]    = useState<string>(saved.current?.symbol    ?? 'BTCUSDT');
  const [timeframe, setTimeframe] = useState<string>(saved.current?.timeframe ?? '5m');
  const [dateFrom,  setDateFrom]  = useState<string>(saved.current?.dateFrom  ?? daysAgoStr(90));
  const [dateTo,    setDateTo]    = useState<string>(saved.current?.dateTo    ?? todayStr());
  const [exchange,  setExchange]  = useState<DatasetExchange>(saved.current?.exchange ?? 'bybit');
  const [paramsHydrated, setParamsHydrated] = useState(saved.current !== null);

  useEffect(() => {
    if (saved.current !== null) return;

    let cancelled = false;
    void cacheRead<DatasetPageParams>(PARAMS_KEY).then((cached) => {
      if (cancelled) return;
      if (cached?.symbol && symbolsAll.includes(cached.symbol)) setSymbol(cached.symbol);
      if (cached?.timeframe && TIMEFRAMES_ALL.includes(cached.timeframe as typeof TIMEFRAMES_ALL[number])) setTimeframe(cached.timeframe);
      if (typeof cached?.dateFrom === 'string' && cached.dateFrom.length > 0) setDateFrom(cached.dateFrom);
      if (typeof cached?.dateTo === 'string' && cached.dateTo.length > 0) setDateTo(cached.dateTo);
      if (cached?.exchange && EXCHANGES.some((item) => item.value === cached.exchange)) setExchange(cached.exchange);
      setParamsHydrated(true);
    }).catch(() => {
      if (!cancelled) setParamsHydrated(true);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const isAllSymbolsMode = symbol === 'ALL';
  const isAllExchangesMode = exchange === 'all';
  const isAggregateSelectionMode = isAllSymbolsMode || isAllExchangesMode;
  const isMultiIngestMode = timeframe === 'ALL' || isAggregateSelectionMode;

  useEffect(() => {
    if (symbol === 'ALL' && timeframe !== 'ALL') {
      setTimeframe('ALL');
    }
  }, [symbol, timeframe]);

  useEffect(() => {
    if (!paramsHydrated) return;

    const nextParams: DatasetPageParams = { symbol, timeframe, dateFrom, dateTo, exchange };
    try { localStorage.setItem(PARAMS_KEY, JSON.stringify(nextParams)); }
    catch { /* ignore */ }
    void cacheWrite(PARAMS_KEY, nextParams, PARAMS_TTL);
  }, [paramsHydrated, symbol, timeframe, dateFrom, dateTo, exchange]);

  // On mount: restore tables from cache immediately
  useEffect(() => {
    void cacheRead<DataTableInfo[]>(CACHE_TABLES_KEY).then(cached => {
      if (cached) setTables(cached);
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // On symbol/timeframe change: restore coverage from cache
  useEffect(() => {
    async function tryRestoreCache() {
      if (isAggregateSelectionMode) {
        setCoverage(null);
        setAllCoverages(null);
        return;
      }
      const [cachedCov, cachedAll] = await Promise.all([
        cacheRead<CoverageResult>(coverageCacheKey(symbol, timeframe, exchange)),
        cacheRead<AllCoverageItem[]>(allCoverageCacheKey(symbol, exchange)),
      ]);
      setCoverage(cachedCov ?? null);
      setAllCoverages(cachedAll ?? null);
    }
    void tryRestoreCache();
  }, [symbol, timeframe, exchange, isAggregateSelectionMode]);

  const [tables,        setTables]        = useState<DataTableInfo[] | null>(null);
  const [coverage,      setCoverage]      = useState<CoverageResult | null>(null);
  const [loadingList,   setLoadingList]   = useState(false);
  const [loadingIngest, setLoadingIngest] = useState(false);
  const [loadingCov,    setLoadingCov]    = useState(false);
  const [loadingDelete, setLoadingDelete] = useState(false);
  const [allCoverages, setAllCoverages] = useState<AllCoverageItem[] | null>(null);
  const [allIngestStatuses, setAllIngestStatuses] = useState<Record<string, TfStatus> | null>(null);
  const [allIngestMeta,    setAllIngestMeta]    = useState<Record<string, TfMeta>>({});
  // Maps dataset scope (`symbol::timeframe`) → job_id for the current
  // multi-job jobs-based ingest.
  const [allIngestJobIds, setAllIngestJobIds] = useState<Record<string, string>>({});
  const allIngestJobIdsRef = useRef<Record<string, string>>({});
  const allIngestErrorCleanupTimersRef = useRef<Record<string, number>>({});
  const handledTerminalErrorJobsRef = useRef<Set<string>>(new Set());
  // Job ID for single-TF job-based ingest.
  const [ingestJobId, setIngestJobId] = useState<string | null>(null);
  const ingestStartedAtRef = useRef<number | null>(null);
  const [loadingExport,  setLoadingExport]  = useState(false);

  // Ingest progress (staged, driven by EVT_DATA_INGEST_PROGRESS events).
  const [ingestStages, setIngestStages] = useState<IngestStage[] | null>(null);
  const ingestCidRef     = useRef<string | null>(null);
  const operationLockRef = useRef(false);

  // Quality audit + repair (per-table, opens on row click).
  const [selectedTable,  setSelectedTable]  = useState<string | null>(null);

  // Live job list (drives AllIngestProgress updates and single-TF ingest display).
  const allJobs = useDatasetJobs();
  // Keep ref in sync so effects can read latest allIngestJobIds without deps.
  allIngestJobIdsRef.current = allIngestJobIds;
  const [qualityReport,  setQualityReport]  = useState<QualityReport | null>(null);
  const [loadingQuality, setLoadingQuality] = useState(false);
  const [repairStages,   setRepairStages]   = useState<RepairStage[] | null>(null);
  const [repairAction,   setRepairAction]   = useState<'load_ohlcv' | 'recompute_features' | null>(null);
  const [loadingRepair,  setLoadingRepair]  = useState(false);
  const repairCidRef = useRef<string | null>(null);

  // Fix All — sequential batch repair of all broken groups across all timeframes
  const fixAllCancelRef  = useRef(false);
  const [fixAllRunning,  setFixAllRunning]  = useState(false);
  const [fixAllProgress, setFixAllProgress] = useState<{
    current:   number;
    total:     number;
    activeOps: { label: string }[];
    completed: { table: string; action: string; ok: boolean; errorMessage?: string }[];
    done:      boolean;
    fixed:     number;
    errors:    number;
  } | null>(null);

  const [isAllMode,         setIsAllMode]         = useState(false);
  const [allQualityResults, setAllQualityResults] = useState<Record<string, QualityReport> | null>(null);
  const [qualityProgress,   setQualityProgress]   = useState<{
    done:     number;
    total:    number;
    slots:    { tf: string; status: 'running' | 'done' | 'error'; message?: string; startedAt?: number }[];
    errors:   number;
    finished: boolean;
    errorLog: { tf: string; message: string }[];
  } | null>(null);

  const clearAllIngestErrorCleanup = (scopeKey: string) => {
    const timerId = allIngestErrorCleanupTimersRef.current[scopeKey];
    if (timerId !== undefined) {
      window.clearTimeout(timerId);
      delete allIngestErrorCleanupTimersRef.current[scopeKey];
    }
  };

  const resetAllIngestErrorCleanup = () => {
    Object.values(allIngestErrorCleanupTimersRef.current).forEach((timerId) => {
      window.clearTimeout(timerId);
    });
    allIngestErrorCleanupTimersRef.current = {};
  };

  const scheduleAllIngestErrorCleanup = (scopeKey: string) => {
    clearAllIngestErrorCleanup(scopeKey);
    allIngestErrorCleanupTimersRef.current[scopeKey] = window.setTimeout(() => {
      delete allIngestErrorCleanupTimersRef.current[scopeKey];
      setAllIngestStatuses(prev => {
        if (!prev || prev[scopeKey] !== 'error') return prev;
        const next = { ...prev };
        delete next[scopeKey];
        return Object.keys(next).length > 0 ? next : null;
      });
      setAllIngestMeta(prev => {
        if (!(scopeKey in prev)) return prev;
        const next = { ...prev };
        delete next[scopeKey];
        return next;
      });
      setAllIngestJobIds(prev => {
        if (!(scopeKey in prev)) return prev;
        const next = { ...prev };
        delete next[scopeKey];
        allIngestJobIdsRef.current = next;
        return next;
      });
    }, ALL_INGEST_ERROR_RETENTION_MS);
  };

  const addDownloadErrorHistory = (scopeKey: string, message: string, durationMs: number) => {
    const parsed = parseIngestScopeKey(scopeKey);
    addEntry({
      action: 'Download',
      params: {
        symbol: parsed.symbol ?? symbol,
        timeframe: parsed.timeframe,
        exchange: parsed.exchange ?? exchange,
        dateFrom,
        dateTo,
      },
      result: `Error: ${shortenMessage(message)}`,
      durationMs,
    });
  };

  useEffect(() => {
    return () => {
      resetAllIngestErrorCleanup();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEvents({
    EVT_DATA_INGEST_PROGRESS: (ev) => {
      if (!ingestCidRef.current || ev.correlation_id !== ingestCidRef.current) return;
      setIngestStages(prev => (prev ?? INITIAL_STAGES).map(s => {
        if (s.id !== ev.stage) return s;
        const status: IngestStage['status'] =
          ev.status === 'done' ? 'done' : ev.status === 'error' ? 'error' : 'running';
        return {
          ...s,
          status,
          progress: ev.status === 'done' ? 100 : ev.progress,
          detail: ev.detail ?? s.detail,
        };
      }));
    },
    EVT_ANALITIC_DATASET_REPAIR_PROGRESS: (ev) => {
      if (!repairCidRef.current || ev.correlation_id !== repairCidRef.current) return;
      setRepairStages(prev => {
        if (!prev) return prev;
        return prev.map(s => {
          if (s.id !== (ev.stage as RepairStageId)) return s;
          const status: RepairStage['status'] =
            ev.status === 'done' ? 'done' : ev.status === 'error' ? 'error' : 'running';
          return {
            ...s,
            status,
            progress: ev.status === 'done' ? 100 : ev.progress,
            detail: ev.detail ?? s.detail,
          };
        });
      });
    },
  });
  // Poll active jobs every 4s. In split deployment the admin can't reach the
  // backend Redpanda, so the EVT_DATA_DATASET_JOB_* SSE stream never arrives —
  // progress and (critically) completion must come from facade polling.
  // refreshActiveJobs also reconciles a locally-running job that has dropped off
  // the active list (i.e. finished) via JOBS_GET, so the bar reaches 100% even
  // with no live events instead of freezing mid-run.
  useDatasetJobsFeed(4000);

  const liveIngestJobIds = allJobs
    .filter(job => job.type === 'ingest' && !job.finished)
    .map(job => job.job_id);
  const trackedIngestJobIdsKey = Array.from(new Set([
    ...(ingestJobId ? [ingestJobId] : []),
    ...Object.values(allIngestJobIds),
    ...liveIngestJobIds,
  ].filter(Boolean))).sort().join('|');

  useEffect(() => {
    if (!trackedIngestJobIdsKey) return;

    let disposed = false;
    const pollTrackedJobs = async () => {
      try {
        await refreshActiveJobs();
        if (disposed) return;

        const jobIds = Array.from(new Set([
          ...(ingestJobId ? [ingestJobId] : []),
          ...Object.values(allIngestJobIdsRef.current),
          ...allJobs
            .filter(job => job.type === 'ingest' && !job.finished)
            .map(job => job.job_id),
        ].filter(Boolean)));
        await refreshJobsByIds(jobIds);
      } catch {
        // SSE stays the primary path. Polling only prevents stale UI.
      }
    };

    void pollTrackedJobs();
    const timer = setInterval(() => {
      void pollTrackedJobs();
    }, 5_000);

    return () => {
      disposed = true;
      clearInterval(timer);
    };
    // NB: `allJobs` deliberately excluded. Including it re-created this interval
    // (and fired pollTrackedJobs) on every single SSE progress event, turning a
    // 5s safety-net poll into a per-event JOBS_LIST+JOBS_GET storm. SSE +
    // useDatasetJobsFeed(4000) are the live path; this is only a slow fallback.
  }, [trackedIngestJobIdsKey, ingestJobId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Tick re-render every second so elapsed timers in running slots update.
  useEffect(() => {
    if (qualityProgress === null || qualityProgress.finished) return;
    if (!qualityProgress.slots.some(s => s.status === 'running')) return;
    const id = setInterval(() => {
      setQualityProgress(prev => (prev ? { ...prev } : prev));
    }, 1000);
    return () => clearInterval(id);
  }, [qualityProgress?.finished, qualityProgress?.slots]); // eslint-disable-line react-hooks/exhaustive-deps

  // Lock-free coverage refresh used by the job-sync effect (after ingest
  // jobs finish) and by the pre-ingest integrity gate. Mirrors the network
  // logic of `handleCheckCoverage` but does NOT take `operationLockRef`,
  // so it composes safely while another operation is in progress.
  const refreshCoverageState = async (): Promise<void> => {
    try {
      if (isAggregateSelectionMode) {
        setCoverage(null);
        setAllCoverages(null);
        return;
      }
      if (timeframe === 'ALL') {
        const results = await Promise.all(
          TIMEFRAMES.map(async tf => {
            try {
              const table = makeTableName(symbol, tf, exchange);
              const cv = await kafkaCall<TableCoverage>(
                Topics.CMD_DATA_DATASET_COVERAGE,
                { table, include_rows: false },
              );
              return {
                tf,
                rows:         cv?.rows ?? 0,
                rows_known:   cv?.rows_known ?? false,
                coverage_pct: getCoveragePct(table, cv),
                date_from:    formatDateFromMs(cv?.min_ts_ms),
                date_to:      formatDateFromMs(cv?.max_ts_ms),
              } satisfies AllCoverageItem;
            } catch {
              return { tf, rows: 0, rows_known: false, coverage_pct: null } satisfies AllCoverageItem;
            }
          }),
        );
        setAllCoverages(results);
        void cacheWrite(allCoverageCacheKey(symbol, exchange), results, CACHE_COVERAGE_TTL);
      } else {
        const table   = makeTableName(symbol, timeframe, exchange);
        const cv = await kafkaCall<TableCoverage>(
          Topics.CMD_DATA_DATASET_COVERAGE,
          { table, include_rows: false },
        );
        const result: CoverageResult = {
          table_name:   table,
          rows:         cv?.rows ?? 0,
          rows_known:   cv?.rows_known ?? false,
          expected:     null,
          coverage_pct: getCoveragePct(table, cv),
          gaps:         null,
        };
        setCoverage(result);
        void cacheWrite(coverageCacheKey(symbol, timeframe, exchange), result, CACHE_COVERAGE_TTL);
      }
    } catch {
      // Best-effort: failures here are non-fatal — the user can click
      // "Проверить покрытие" manually if numbers look off.
    }
  };

  // ── Job sync: map live job events to per-TF status/meta and single-TF stages ──
  useEffect(() => {
    // ALL-mode: update per-TF statuses from job events.
    const ids = Object.entries(allIngestJobIdsRef.current);
    if (ids.length > 0) {
      for (const [scopeKey, jobId] of ids) {
        const job = allJobs.find(j => j.job_id === jobId);
        if (!job) continue;
        if (job.finished) {
          const isSuccessful = job.status === 'succeeded' || job.status === 'skipped';
          const newStatus: TfStatus = isSuccessful ? 'done' : 'error';
          clearAllIngestErrorCleanup(scopeKey);
          setAllIngestStatuses(prev =>
            !prev || prev[scopeKey] === newStatus ? prev : { ...prev, [scopeKey]: newStatus },
          );
          setAllIngestMeta(prev => {
            const existing = prev[scopeKey];
            if (existing?.endedAt !== undefined) return prev;
            return {
              ...prev,
              [scopeKey]: {
                ...(existing ?? { startedAt: Date.now() }),
                endedAt: Date.now(),
                rows:  isSuccessful ? (job.completed ?? 0) : existing?.rows,
                pct:   isSuccessful ? 100 : existing?.pct,
                stage: job.stage ?? existing?.stage,
                detail: job.detail ?? existing?.detail,
                error: !isSuccessful ? (job.error_message ?? 'failed') : undefined,
              },
            };
          });
          if (!isSuccessful) {
            scheduleAllIngestErrorCleanup(scopeKey);
            if (!handledTerminalErrorJobsRef.current.has(job.job_id)) {
              handledTerminalErrorJobsRef.current.add(job.job_id);
              addDownloadErrorHistory(
                scopeKey,
                job.error_message ?? job.detail ?? 'Ingest failed',
                Math.max(0, Date.now() - (allIngestMeta[scopeKey]?.startedAt ?? Date.now())),
              );
            }
          }
        } else if (job.status === 'running') {
          // Honest transition: queued → running on first scheduler dispatch.
          clearAllIngestErrorCleanup(scopeKey);
          setAllIngestStatuses(prev =>
            !prev || prev[scopeKey] === 'running' ? prev : { ...prev, [scopeKey]: 'running' },
          );
          setAllIngestMeta(prev => {
            const m = prev[scopeKey];
            if (
              m?.pct === job.progress &&
              m?.stage === (job.stage ?? undefined) &&
              m?.detail === (job.detail ?? undefined) &&
              m?.runningAt !== undefined
            ) return prev;
            return {
              ...prev,
              [scopeKey]: {
                ...(m ?? { startedAt: Date.now() }),
                runningAt: m?.runningAt ?? Date.now(),
                pct: job.progress,
                stage: job.stage ?? undefined,
                detail: job.detail ?? undefined,
              },
            };
          });
        } else if (job.status === 'queued') {
          // Job exists in DB queue but scheduler hasn't picked it up yet.
          clearAllIngestErrorCleanup(scopeKey);
          setAllIngestStatuses(prev =>
            !prev || prev[scopeKey] === 'queued' ? prev : { ...prev, [scopeKey]: 'queued' },
          );
        }
      }
      // When all jobs terminal, refresh coverage and clear job IDs.
      const allTerminal = ids.every(([, jid]) => {
        const j = allJobs.find(x => x.job_id === jid);
        return j?.finished === true;
      });
      if (allTerminal) {
        setAllIngestJobIds({});
        setLoadingIngest(false);
        operationLockRef.current = false;
        void handleListTables();
        // Refresh actual coverage so the right-hand panel reflects DB state
        // immediately, without requiring the user to click "Проверить покрытие".
        void refreshCoverageState();
      }
    }

    // Single-TF mode: update ingestStages from job progress.
    if (ingestJobId) {
      const job = allJobs.find(j => j.job_id === ingestJobId);
      if (job) {
        if (job.finished) {
          // Drive the strip to its terminal state (all stages done/100 on
          // success, error stage on failure) so it never freezes mid-run when
          // the completion arrives via SSE or the reconcile poll.
          setIngestStages(prev => mapJobToStages(prev ?? INITIAL_STAGES, job));
          setLoadingIngest(false);
          operationLockRef.current = false;
          const isSuccessful = job.status === 'succeeded' || job.status === 'skipped';
          if (isSuccessful) {
            toast(formatIngestSuccessToast(job.completed), 'success');
            void handleListTables();
            void refreshCoverageState();
          } else {
            const message = job.error_message ?? 'Ingest failed';
            toast(message, 'error');
            if (!handledTerminalErrorJobsRef.current.has(job.job_id)) {
              handledTerminalErrorJobsRef.current.add(job.job_id);
              addDownloadErrorHistory(
                timeframe,
                message,
                Math.max(0, Date.now() - (ingestStartedAtRef.current ?? Date.now())),
              );
            }
          }
          ingestStartedAtRef.current = null;
          setIngestJobId(null);
        } else if (job.status === 'running') {
          // Initialise the staged progress strip only once the scheduler
          // has actually dispatched the job. This avoids the misleading
          // "all stages pending while job sits in queue" UI.
          setIngestStages(prev => mapJobToStages(prev ?? INITIAL_STAGES, job));
        }
        // queued: leave ingestStages = null so the UI shows "queued" placeholder.
      }
    }
  }, [allJobs]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isAggregateSelectionMode) return;
    setCoverage(null);
    setAllCoverages(null);
    setSelectedTable(null);
    setQualityReport(null);
    setAllQualityResults(null);
    setQualityProgress(null);
    setRepairStages(null);
    setIsAllMode(false);
  }, [isAggregateSelectionMode]);

  const handleListTables = async () => {
    if (operationLockRef.current) return;
    operationLockRef.current = true;
    setLoadingList(true);
    const t0 = Date.now();
    try {
      // Backend enriches every entry with bounds metadata in a single round-trip.
      // Exact counts stay on the explicit coverage path only.
      // in a single round-trip. Legacy string[] is still tolerated here so a
      // rolling deploy where DataService is older than Admin doesn't break the
      // page; that fallback should be removed once both services are in lockstep.
      const res = await kafkaCall<{
        tables: Array<string | {
          table_name: string;
          rows?: number;
          rows_known?: boolean;
          coverage_pct?: number;
          date_from?: string | null;
          date_to?: string | null;
        }>;
      }>(
        Topics.CMD_DATA_DATASET_LIST_TABLES,
        {},
      );

      const raw = res.tables ?? [];
      const enriched = raw.filter((x): x is { table_name: string; rows?: number; rows_known?: boolean; coverage_pct?: number; date_from?: string | null; date_to?: string | null } => typeof x !== 'string');
      const legacyNames = raw.filter((x): x is string => typeof x === 'string');

      const fromEnriched: DataTableInfo[] = enriched.map(t => ({
        table_name:   t.table_name,
        rows:         t.rows ?? 0,
        rows_known:   t.rows_known ?? true,
        coverage_pct: t.coverage_pct ?? null,
        date_from:    t.date_from ?? undefined,
        date_to:      t.date_to ?? undefined,
      }));

      // Transitional fallback: only fan-out coverage for legacy string entries.
      // When all entries are enriched (the happy path) this loop is empty and
      // we make exactly ONE Kafka round-trip.
      const fromLegacy: DataTableInfo[] = await Promise.all(
        legacyNames.map(async name => {
          try {
            const cv = await kafkaCall<TableCoverage>(
              Topics.CMD_DATA_DATASET_COVERAGE,
              { table: name, include_rows: false },
            );
            return {
              table_name:   name,
              rows:         cv?.rows ?? 0,
              rows_known:   cv?.rows_known ?? false,
              coverage_pct: getCoveragePct(name, cv),
              date_from:    formatDateFromMs(cv?.min_ts_ms),
              date_to:      formatDateFromMs(cv?.max_ts_ms),
            };
          } catch {
            return { table_name: name, rows: 0, rows_known: false, coverage_pct: null };
          }
        }),
      );

      const infos = [...fromEnriched, ...fromLegacy];
      setTables(infos);
      void cacheWrite(CACHE_TABLES_KEY, infos, CACHE_TABLES_TTL);
      addEntry({ action: 'Check', params: { symbol, timeframe, exchange }, result: `${infos.length} tables`, durationMs: Date.now() - t0 });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
      addEntry({ action: 'Check', params: { symbol, timeframe, exchange }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
    } finally {
      operationLockRef.current = false;
      setLoadingList(false);
    }
  };

  const handleCheckCoverage = async () => {
    if (isAggregateSelectionMode) {
      toast('Для режимов Symbol=ALL или Exchange=ALL доступен только Ingest. Выберите конкретный датасет и биржу для coverage.', 'info');
      return;
    }
    if (operationLockRef.current) return;
    operationLockRef.current = true;
    setLoadingCov(true);
    const t0 = Date.now();
    try {
      const startMs = new Date(dateFrom).getTime();
      const endMs   = new Date(`${dateTo}T23:59:59`).getTime();
      if (timeframe === 'ALL') {
        setCoverage(null);
        const results = await Promise.all(
          TIMEFRAMES.map(async tf => {
            try {
              const table = makeTableName(symbol, tf, exchange);
              const cv = await kafkaCall<TableCoverage>(
                Topics.CMD_DATA_DATASET_COVERAGE,
                { table, timeframe: tf, start_ms: startMs, end_ms: endMs },
              );
              return {
                tf,
                rows:         cv?.rows_in_range ?? cv?.rows ?? 0,
                rows_known:   cv?.rows_known ?? true,
                coverage_pct: getCoveragePct(table, cv),
                date_from:    formatDateFromMs(cv?.min_ts_ms),
                date_to:      formatDateFromMs(cv?.max_ts_ms),
              } satisfies AllCoverageItem;
            } catch {
              return { tf, rows: 0, rows_known: true, coverage_pct: null } satisfies AllCoverageItem;
            }
          }),
        );
        setAllCoverages(results);
        void cacheWrite(allCoverageCacheKey(symbol, exchange), results, CACHE_COVERAGE_TTL);
        addEntry({ action: 'Check', params: { symbol, timeframe: 'ALL', exchange, dateFrom, dateTo }, result: `${results.length} timeframes`, durationMs: Date.now() - t0 });
      } else {
        setAllCoverages(null);
        const table   = makeTableName(symbol, timeframe, exchange);

        const cv = await kafkaCall<TableCoverage>(
          Topics.CMD_DATA_DATASET_COVERAGE,
          { table, timeframe, start_ms: startMs, end_ms: endMs },
        );

        const stepMs = TF_STEP_MS[timeframe];
        const expected = cv?.expected ?? (stepMs && endMs > startMs
          ? Math.max(0, Math.floor((endMs - startMs) / stepMs) + 1)
          : 0);
        const rows = cv?.rows_in_range ?? cv?.rows ?? 0;
        const coveragePct = getCoveragePct(table, cv);
        const gaps = cv?.gaps ?? Math.max(0, expected - rows);

        const result: CoverageResult = {
          table_name:   table,
          rows,
          rows_known:   cv?.rows_known ?? true,
          expected,
          coverage_pct: coveragePct,
          gaps,
        };
        setCoverage(result);
        void cacheWrite(coverageCacheKey(symbol, timeframe, exchange), result, CACHE_COVERAGE_TTL);
        addEntry({ action: 'Check', params: { symbol, timeframe, exchange, dateFrom, dateTo }, result: coveragePct != null ? `${coveragePct.toFixed(1)}% coverage` : 'Coverage metadata loaded', durationMs: Date.now() - t0 });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
      addEntry({ action: 'Check', params: { symbol, timeframe, exchange, dateFrom, dateTo }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
    } finally {
      operationLockRef.current = false;
      setLoadingCov(false);
    }
  };

  const handleIngest = async () => {
    if (operationLockRef.current) return;
    operationLockRef.current = true;
    setLoadingIngest(true);
    let keepIngestBusy = false;
    const t0 = Date.now();
    try {
      // Mandatory pre-ingest integrity step: refresh actual coverage
      // numbers BEFORE creating any jobs so the user can see the real
      // state (and so we never blow away an existing healthy display
      // with a fake "0 rows" skeleton). Failures are non-fatal.
      await refreshCoverageState();

      if (isMultiIngestMode) {
        const selectedExchanges = exchange === 'all'
          ? ACTIVE_EXCHANGES.map((item) => item.value)
          : [exchange];
        const selectedSymbols = symbol === 'ALL' ? [...symbols] : [symbol];
        const selectedTimeframes = timeframe === 'ALL' || symbol === 'ALL'
          ? [...TIMEFRAMES] as string[]
          : [timeframe];
        const targets = selectedExchanges.flatMap((targetExchange) =>
          selectedSymbols.flatMap((targetSymbol) =>
            selectedTimeframes.map((targetTimeframe) => ({
              exchange: targetExchange,
              symbol: targetSymbol,
              timeframe: targetTimeframe,
              scopeKey: buildIngestScopeKey(targetExchange, targetSymbol, targetTimeframe),
            })),
          ),
        );
        resetAllIngestErrorCleanup();

        // Initialize per-TF status dictionary. 'pending' = local pre-Kafka
        // placeholder; flips to 'queued' on JOBS_START reply, then
        // 'running' once the scheduler dispatches the job (see job-sync
        // useEffect). Existing coverage data is intentionally preserved.
        const initialStatuses: Record<string, TfStatus> = {};
        for (const target of targets) initialStatuses[target.scopeKey] = 'pending';
        setAllIngestStatuses(initialStatuses);
        setAllIngestMeta({});
        setAllIngestJobIds({});
        ingestCidRef.current = null;
        setIngestStages(null);

        const startMs = new Date(dateFrom).getTime();
        const endMs   = new Date(dateTo + 'T23:59:59').getTime();
        const newJobIds: Record<string, string> = {};

        for (const target of targets) {
          const scopeLabel = `${EXCHANGES.find((item) => item.value === target.exchange)?.label ?? target.exchange} ${target.symbol} ${target.timeframe}`;
          try {
            // 5s is plenty: the data-service replies synchronously after a
            // single INSERT. A longer wait would only mask backend bugs.
            const res = await kafkaCall<{
              job_id?: string;
              status?: string;
              deduped?: boolean;
              error?: string;
              code?: string;
            }>(
              Topics.CMD_DATA_DATASET_JOBS_START,
              {
                type: 'ingest',
                params: { symbol: target.symbol, timeframe: target.timeframe, start_ms: startMs, end_ms: endMs, exchange: target.exchange },
                target_symbol: target.symbol, target_timeframe: target.timeframe,
                target_exchange: target.exchange,
                target_start_ms: startMs, target_end_ms: endMs,
                created_by: 'admin_ui',
              },
              { timeoutMs: 5_000 },
            );
            // Backend returns { error, code } on validation / schema / DB
            // errors. Treat that as a real failure for THIS TF (no fake
            // "running" status) so ALL-mode honestly reflects what was
            // actually started.
            if (res.error || !res.job_id) {
              const msg = res.error ?? 'no job_id in reply';
              setAllIngestStatuses(prev => ({ ...(prev ?? {}), [target.scopeKey]: 'error' }));
              setAllIngestMeta(prev => ({ ...prev, [target.scopeKey]: { startedAt: Date.now(), error: msg } }));
              scheduleAllIngestErrorCleanup(target.scopeKey);
              addDownloadErrorHistory(target.scopeKey, msg, Date.now() - t0);
              toast(`${scopeLabel}: не удалось запустить job — ${msg}`, 'info');
              continue;
            }
            newJobIds[target.scopeKey] = res.job_id;
            // Honest status: queued, NOT running. The scheduler hasn't
            // necessarily picked the job up yet; the UI flips to
            // 'running' only when the first progress event arrives.
            clearAllIngestErrorCleanup(target.scopeKey);
            setAllIngestStatuses(prev => ({ ...(prev ?? {}), [target.scopeKey]: 'queued' }));
            setAllIngestMeta(prev => ({ ...prev, [target.scopeKey]: { startedAt: Date.now() } }));
            seedQueuedJob({
              jobId: res.job_id,
              type: 'ingest',
              target_table: makeTableName(target.symbol, target.timeframe, target.exchange),
            });
            if (res.deduped) toast(`${scopeLabel}: уже загружается (job deduped)`, 'info');
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            setAllIngestStatuses(prev => ({ ...(prev ?? {}), [target.scopeKey]: 'error' }));
            setAllIngestMeta(prev => ({ ...prev, [target.scopeKey]: { startedAt: Date.now(), error: msg } }));
            scheduleAllIngestErrorCleanup(target.scopeKey);
            addDownloadErrorHistory(target.scopeKey, msg, Date.now() - t0);
            toast(`${scopeLabel}: не удалось запустить job — ${msg}`, 'info');
          }
        }

        allIngestJobIdsRef.current = newJobIds;
        setAllIngestJobIds(newJobIds);
        keepIngestBusy = Object.keys(newJobIds).length > 0;
        addEntry({
          action: 'Download',
          params: { symbol, timeframe, exchange, dateFrom, dateTo },
          result: `Started ${Object.keys(newJobIds).length} ingest jobs`,
          durationMs: Date.now() - t0,
        });
        // Coverage refresh and unlock happen in the job-sync useEffect when all jobs finish.
      } else {
        const _sMs = new Date(dateFrom).getTime();
        const _eMs = new Date(dateTo + 'T23:59:59').getTime();
        // Don't seed INITIAL_STAGES yet — wait until job actually
        // transitions to 'running' so we don't lie about progress.
        setIngestStages(null);
        const res = await kafkaCall<{
          job_id?: string;
          status?: string;
          deduped?: boolean;
          error?: string;
          code?: string;
        }>(
          Topics.CMD_DATA_DATASET_JOBS_START,
          {
            type: 'ingest',
            params: { symbol, timeframe, start_ms: _sMs, end_ms: _eMs, exchange },
            target_symbol: symbol, target_timeframe: timeframe,
            target_exchange: exchange,
            target_start_ms: _sMs, target_end_ms: _eMs,
            created_by: 'admin_ui',
          },
          { timeoutMs: 5_000 },
        );
        // Surface backend error/code instead of pretending the job started.
        if (res.error || !res.job_id) {
          throw new Error(res.error ?? 'no job_id in reply');
        }
        ingestStartedAtRef.current = Date.now();
        setIngestJobId(res.job_id);
        seedQueuedJob({
          jobId: res.job_id,
          type: 'ingest',
          target_table: makeTableName(symbol, timeframe, exchange),
        });
        keepIngestBusy = true;
        if (res.deduped) {
          toast(`Уже загружается (job ${res.job_id.slice(0, 8)}…) — deduped`, 'info');
        } else {
          toast(`Job в очереди (${res.job_id.slice(0, 8)}…), ожидает планировщика`, 'success');
        }
        addEntry({
          action: 'Download',
          params: { symbol, timeframe, exchange, dateFrom, dateTo },
          result: `Job ${res.job_id.slice(0, 8)} started`,
          durationMs: Date.now() - t0,
        });
        // Keep loadingIngest=true — job-sync useEffect clears it when job finishes.
        return;
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
      addEntry({ action: 'Download', params: { symbol, timeframe, exchange, dateFrom, dateTo }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
    } finally {
      if (!keepIngestBusy) {
        operationLockRef.current = false;
        setLoadingIngest(false);
      }
    }
  };

  const handleDeleteRows = async () => {
    if (isAggregateSelectionMode) {
      toast('Для режимов Symbol=ALL или Exchange=ALL доступен только Ingest. Очистка требует конкретный датасет и биржу.', 'info');
      return;
    }
    if (operationLockRef.current) return;
    operationLockRef.current = true;
    if (timeframe === 'ALL') {
      const confirmed = typeof window !== 'undefined' && window.confirm(
        `Удалить все строки по ВСЕМ таймфреймам для ${symbol}?\nЭто удалит данные из ${TIMEFRAMES.length} таблиц и не может быть отменено.`,
      );
      if (!confirmed) { operationLockRef.current = false; return; }

      setLoadingDelete(true);
      const t0 = Date.now();
      try {
        let totalDeleted = 0;
        let successes = 0;
        for (const tf of TIMEFRAMES) {
          const table = makeTableName(symbol, tf, exchange);
          try {
            const res = await kafkaCall<{ rows_deleted?: number; error?: string }>(
              Topics.CMD_DATA_DATASET_DELETE_ROWS,
              { table },
            );
            if (res.error) throw new Error(res.error);
            totalDeleted += res.rows_deleted ?? 0;
            successes++;
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            toast(`${tf}: ${msg}`, 'info');
          }
        }
        const msg = `Deleted ${totalDeleted.toLocaleString()} rows across ${successes} timeframes`;
        toast(msg, 'success');
        addEntry({ action: 'Check', params: { symbol, timeframe: 'ALL', exchange }, result: msg, durationMs: Date.now() - t0 });
      } finally {
        operationLockRef.current = false;
        setLoadingDelete(false);
        // Refresh AFTER releasing the lock — handleListTables early-returns while
        // operationLockRef is held, so calling it inside the try was a no-op
        // (the table list stayed stale after a delete).
        void handleListTables();
      }
    } else {
      const table = makeTableName(symbol, timeframe, exchange);
      const confirmed = typeof window !== 'undefined' && window.confirm(
        `Удалить все строки из таблицы ${table}? Это действие нельзя отменить.`,
      );
      if (!confirmed) { operationLockRef.current = false; return; }

      setLoadingDelete(true);
      const t0 = Date.now();
      try {
        const res = await kafkaCall<{ rows_deleted?: number; error?: string }>(
          Topics.CMD_DATA_DATASET_DELETE_ROWS,
          { table },
        );
        if (res.error) throw new Error(res.error);
        const count = res.rows_deleted ?? 0;
        const msg = `Deleted ${count.toLocaleString()} rows from ${table}`;
        toast(msg, 'success');
        addEntry({ action: 'Check', params: { symbol, timeframe, exchange }, result: msg, durationMs: Date.now() - t0 });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toast(msg, 'error');
        addEntry({ action: 'Check', params: { symbol, timeframe, exchange }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
      } finally {
        operationLockRef.current = false;
        setLoadingDelete(false);
        // Refresh after the lock is released (see ALL-branch note above).
        void handleListTables();
      }
    }
  };

  const handleExportCsv = async () => {
    if (isAggregateSelectionMode) {
      toast('Для режимов Symbol=ALL или Exchange=ALL export не поддерживается. Выберите конкретный датасет и биржу.', 'info');
      return;
    }
    if (operationLockRef.current) return;
    operationLockRef.current = true;
    if (!dateFrom || !dateTo) {
      operationLockRef.current = false;
      toast('Укажите даты From/To', 'info');
      return;
    }

    const startMs = new Date(dateFrom).getTime();
    const endMs   = new Date(dateTo).getTime() + 86_400_000 - 1;
    const base    = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

    let url: string;
    let filename: string;
    if (timeframe === 'ALL') {
      url = `${base}/api/export/csv?symbol=${encodeURIComponent(symbol)}&timeframe=ALL&exchange=${encodeURIComponent(exchange)}`
          + `&start_ms=${startMs}&end_ms=${endMs}`;
      filename = `${symbol}_ALL.zip`;
    } else {
      const table = makeTableName(symbol, timeframe, exchange);
      url = `${base}/api/export/csv?table=${encodeURIComponent(table)}`
          + `&start_ms=${startMs}&end_ms=${endMs}`;
      filename = `${table}.csv`;
    }

    setLoadingExport(true);
    try {
      // Admin holds the connection open while Kafka + DataService + MinIO
      // complete; only a tiny JSON { presigned_url } returns — no file bytes.
      const res = await fetch(url);
      if (!res.ok) {
        const j = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        throw new Error((j as { error?: string }).error ?? `HTTP ${res.status}`);
      }

      const { presigned_url } = await res.json() as { presigned_url?: string };
      if (!presigned_url) throw new Error('Сервер не вернул presigned_url');

      // Браузер качает напрямую из object storage через тот же внешний
      // origin (infra-nginx → /modelline-blobs/*). Никакой нормализации
      // host'а здесь нет: data-service сам выдаёт browser-reachable URL.
      const a    = document.createElement('a');
      a.href     = presigned_url;
      a.download = filename;
      a.rel      = 'noopener noreferrer';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);

      toast('Загрузка началась', 'success');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
    } finally {
      operationLockRef.current = false;
      setLoadingExport(false);
    }
  };

  const isBusy = loadingList || loadingIngest || loadingCov || loadingDelete || loadingExport;
  const datasetHistory = history.filter(h => h.action === 'Check' || h.action === 'Download').slice(0, 20);

  // ── Quality audit & repair ────────────────────────────────────────────────

  const runQualityCheck = async (table: string): Promise<QualityReport | null> => {
    if (!table) return null;
    setLoadingQuality(true);
    setQualityReport(null);
    try {
      const res = await kafkaCall<QualityReport | { error: string }>(
        Topics.CMD_ANALITIC_DATASET_QUALITY_CHECK,
        { table },
        { timeoutMs: 60_000 },
      );
      if ('error' in res) {
        toast(`Quality check failed: ${res.error}`, 'error');
        return null;
      }
      setQualityReport(res);
      // Sync displayed coverage when the user runs an integrity check.
      void refreshCoverageState();
      return res;
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'error');
      return null;
    } finally {
      setLoadingQuality(false);
    }
  };

  const runRepair = async (table: string, action: 'load_ohlcv' | 'recompute_features') => {
    if (loadingRepair) return;
    const parsed = parseTableName(table);
    if (!parsed) {
      toast(`Cannot parse table name: ${table}`, 'error');
      return;
    }
    const cid = newCorrelationId();
    repairCidRef.current = cid;
    setRepairAction(action);
    setRepairStages(action === 'load_ohlcv'
      ? INITIAL_REPAIR_STAGES_OHLCV.map(s => ({ ...s }))
      : INITIAL_REPAIR_STAGES_RECOMPUTE.map(s => ({ ...s })));
    setLoadingRepair(true);
    const t0 = Date.now();
    try {
      if (action === 'load_ohlcv') {
        const startMs = new Date(dateFrom).getTime();
        const endMs   = new Date(dateTo).getTime() + 24 * 60 * 60 * 1000 - 1;
        const reply = await kafkaCall<{ rows_affected?: number; error?: string }>(
          Topics.CMD_ANALITIC_DATASET_LOAD_OHLCV,
          { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange, start_ms: startMs, end_ms: endMs },
          { correlationId: cid, timeoutMs: 600_000 },
        );
        if (reply.error) {
          toast(`Load OHLCV failed: ${reply.error}`, 'error');
        } else {
          toast(`OHLCV upserted: ${reply.rows_affected ?? 0} rows`, 'success');
        }
        addEntry({
          action: 'Download',
          params: { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange, dateFrom, dateTo },
          result: reply.error ? `Error: ${reply.error}` : `${reply.rows_affected ?? 0} rows`,
          durationMs: Date.now() - t0,
        });
      } else {
        const reply = await kafkaCall<{ rows_updated?: number; error?: string }>(
          Topics.CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES,
          { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange },
          { correlationId: cid, timeoutMs: 600_000 },
        );
        if (reply.error) {
          toast(`Recompute failed: ${reply.error}`, 'error');
        } else {
          toast(`Features recomputed: ${reply.rows_updated ?? 0} rows`, 'success');
        }
        addEntry({
          action: 'Download',
          params: { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange },
          result: reply.error ? `Error: ${reply.error}` : `${reply.rows_updated ?? 0} rows`,
          durationMs: Date.now() - t0,
        });
      }
      // Re-audit after repair so the user sees the updated fill ratios.
      const fresh = await runQualityCheck(table);
      if (fresh) {
        setAllQualityResults(prev => (prev && table in prev) ? { ...prev, [table]: fresh } : prev);
      }
      // Sync displayed coverage after a successful repair.
      void refreshCoverageState();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'error');
    } finally {
      setLoadingRepair(false);
      repairCidRef.current = null;
    }
  };

  /** Same repair logic as runRepair but without per-repair UI state. Used by handleFixAll. */
  const runRepairSilent = async (table: string, action: 'load_ohlcv' | 'recompute_features'): Promise<void> => {
    const parsed = parseTableName(table);
    if (!parsed) throw new Error(`Cannot parse table name: ${table}`);
    const t0 = Date.now();
    if (action === 'load_ohlcv') {
      const startMs = new Date(dateFrom).getTime();
      const endMs   = new Date(dateTo).getTime() + 24 * 60 * 60 * 1000 - 1;
      const reply = await kafkaCall<{ rows_affected?: number; error?: string }>(
        Topics.CMD_ANALITIC_DATASET_LOAD_OHLCV,
        { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange, start_ms: startMs, end_ms: endMs },
        { timeoutMs: 600_000 },
      );
      addEntry({ action: 'Download', params: { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange, dateFrom, dateTo }, result: reply.error ? `Error: ${reply.error}` : `${reply.rows_affected ?? 0} rows`, durationMs: Date.now() - t0 });
      if (reply.error) throw new Error(reply.error);
    } else {
      const reply = await kafkaCall<{ rows_updated?: number; error?: string }>(
        Topics.CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES,
        { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange },
        { timeoutMs: 600_000 },
      );
      addEntry({ action: 'Download', params: { symbol: parsed.symbol, timeframe: parsed.timeframe, exchange: parsed.exchange }, result: reply.error ? `Error: ${reply.error}` : `${reply.rows_updated ?? 0} rows`, durationMs: Date.now() - t0 });
      if (reply.error) throw new Error(reply.error);
    }
    // Re-audit so the grid reflects the updated fill ratios.
    try {
      const res = await kafkaCall<QualityReport | { error: string }>(
        Topics.CMD_ANALITIC_DATASET_QUALITY_CHECK,
        { table },
        { timeoutMs: 60_000 },
      );
      if (!('error' in res)) {
        setAllQualityResults(prev => prev ? { ...prev, [table]: res as QualityReport } : prev);
      }
    } catch { /* non-fatal */ }
  };

  const handleRepairDataset = async () => {
    if (isAggregateSelectionMode) {
      toast('Для режимов Symbol=ALL или Exchange=ALL доступен только Ingest. Выберите конкретный датасет и биржу для проверки целостности.', 'info');
      return;
    }
    if (loadingQuality || loadingRepair) return;
    setQualityReport(null);
    setAllQualityResults(null);
    setRepairStages(null);
    setQualityProgress(null);
    if (timeframe !== 'ALL') {
      setIsAllMode(false);
      const table = makeTableName(symbol, timeframe, exchange);
      setSelectedTable(table);
      await runQualityCheck(table);
    } else {
      setIsAllMode(true);
      setSelectedTable(null);
      const tfs = [...TIMEFRAMES] as string[];
      const total = tfs.length;
      const CONCURRENCY = 2;
      setQualityProgress({ done: 0, total, slots: [], errors: 0, finished: false, errorLog: [] });
      setLoadingQuality(true);
      const results: Record<string, QualityReport> = {};
      let totalErrors = 0;
      try {
        for (let i = 0; i < tfs.length; i += CONCURRENCY) {
          const batch = tfs.slice(i, i + CONCURRENCY);

          // Mark both TFs in this batch as running before launching.
          const batchStartedAt = Date.now();
          setQualityProgress(prev => prev
            ? { ...prev, slots: batch.map(tf => ({ tf, status: 'running' as const, startedAt: batchStartedAt })) }
            : prev,
          );

          const batchResults = await Promise.allSettled(
            batch.map(async tf => {
              const table = makeTableName(symbol, tf, exchange);
              const res = await kafkaCall<QualityReport | { error: string }>(
                Topics.CMD_ANALITIC_DATASET_QUALITY_CHECK,
                { table },
                { timeoutMs: 60_000 },
              );
              if ('error' in res) throw new Error(res.error);
              return { tf, table, report: res as QualityReport };
            }),
          );

          const completedSlots: { tf: string; status: 'done' | 'error'; message?: string }[] = [];
          const newErrors: { tf: string; message: string }[] = [];
          let batchErrors = 0;
          for (let j = 0; j < batchResults.length; j++) {
            const r = batchResults[j];
            if (r.status === 'fulfilled') {
              results[r.value.table] = r.value.report;
              completedSlots.push({ tf: batch[j], status: 'done' });
            } else {
              const message = (r.reason as Error)?.message ?? 'Неизвестная ошибка';
              batchErrors++;
              completedSlots.push({ tf: batch[j], status: 'error', message });
              newErrors.push({ tf: batch[j], message });
            }
          }
          totalErrors += batchErrors;

          // done increments after completion, not before.
          setQualityProgress(prev => prev
            ? { ...prev, done: prev.done + batch.length, slots: completedSlots, errors: totalErrors, errorLog: [...(prev.errorLog ?? []), ...newErrors] }
            : prev,
          );
        }

        // Hold at 100% for a beat so the user sees the completed state.
        await new Promise<void>(resolve => setTimeout(resolve, 900));
      } finally {
        setLoadingQuality(false);
        if (totalErrors > 0) {
          // Keep the block visible with error details; user closes it manually.
          setQualityProgress(prev => prev ? { ...prev, finished: true } : prev);
        } else {
          setQualityProgress(null);
        }
        setAllQualityResults(results);
      }
    }
  };

  const handleFixAll = async () => {
    if (!allQualityResults || fixAllRunning) return;
    type FixOp = { table: string; action: 'load_ohlcv' | 'recompute_features'; label: string };
    const ops: FixOp[] = [];
    for (const [table, report] of Object.entries(allQualityResults)) {
      const broken = report.groups.filter(g => g.status !== 'full');
      if (broken.length === 0) continue;
      // Fixed order: load_ohlcv first, recompute_features second. One per type per table.
      if (broken.some(g => g.repair_action === 'load_ohlcv'))
        ops.push({ table, action: 'load_ohlcv', label: `${table}: Загрузить OHLCV` });
      if (broken.some(g => g.repair_action === 'recompute_features'))
        ops.push({ table, action: 'recompute_features', label: `${table}: Пересчитать фичи` });
    }
    if (ops.length === 0) return;

    const CONCURRENCY = 4;
    fixAllCancelRef.current = false;
    setFixAllRunning(true);
    setFixAllProgress({ current: 0, total: ops.length, activeOps: [], completed: [], done: false, fixed: 0, errors: 0 });

    // Scheduler state — mutated only in microtask callbacks (no true JS races)
    const pending = [...ops];
    const runningTables = new Set<string>();
    const active: { table: string; action: string; label: string }[] = [];
    let runningCount = 0;
    let doneCount    = 0;
    let fixedCount   = 0;
    let errorCount   = 0;
    const completedList: { table: string; action: string; ok: boolean; errorMessage?: string }[] = [];

    await new Promise<void>(resolve => {
      let resolved = false;
      const finish = () => {
        if (resolved) return;
        resolved = true;
        setFixAllRunning(false);
        setFixAllProgress(prev =>
          prev ? { ...prev, done: true, current: doneCount, activeOps: [], fixed: fixedCount, errors: errorCount } : prev,
        );
        resolve();
      };

      const trySchedule = () => {
        while (!fixAllCancelRef.current && runningCount < CONCURRENCY && pending.length > 0) {
          const idx = pending.findIndex(op => !runningTables.has(op.table));
          if (idx === -1) break; // All remaining ops blocked by per-table lock
          const op = pending.splice(idx, 1)[0];
          runningTables.add(op.table);
          runningCount++;
          active.push({ table: op.table, action: op.action, label: op.label });
          setFixAllProgress(prev =>
            prev ? { ...prev, activeOps: active.map(a => ({ label: a.label })) } : prev,
          );
          runRepairSilent(op.table, op.action)
            .then(()  => { fixedCount++;  completedList.push({ table: op.table, action: op.action, ok: true  }); })
            .catch((err: unknown) => { errorCount++;  completedList.push({ table: op.table, action: op.action, ok: false, errorMessage: err instanceof Error ? err.message : String(err) }); })
            .finally(() => {
              doneCount++;
              runningCount--;
              runningTables.delete(op.table);
              const li = active.findIndex(a => a.table === op.table && a.action === op.action);
              if (li !== -1) active.splice(li, 1);
              setFixAllProgress(prev =>
                prev ? {
                  ...prev,
                  current:   doneCount,
                  activeOps: active.map(a => ({ label: a.label })),
                  completed: [...completedList],
                  fixed:     fixedCount,
                  errors:    errorCount,
                } : prev,
              );
              if (runningCount === 0 && (pending.length === 0 || fixAllCancelRef.current)) {
                finish();
                return;
              }
              trySchedule();
            });
        }
        if (runningCount === 0) finish();
      };

      trySchedule();
    });
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6 w-full">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Dataset</h1>
      </header>

      {/* ── 2-column: Config left | Coverage right ── */}
      <div className="grid grid-cols-1 lg:grid-cols-[380px,1fr] gap-4 sm:gap-6 items-start">

        {/* Left — fixed config card */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">Dataset Configuration</CardTitle>
          </CardHeader>
          <Separator />
          <CardContent className="p-3 sm:p-4 pt-4 space-y-4">
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Symbol</label>
                <Select value={symbol} onValueChange={setSymbol}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{symbolsAll.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Timeframe</label>
                <Select value={timeframe} onValueChange={setTimeframe}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{(symbol === 'ALL' ? ['ALL'] : TIMEFRAMES_ALL).map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Date From</label>
                <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="w-full" style={{ colorScheme: 'dark' }} />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Date To</label>
                <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="w-full" style={{ colorScheme: 'dark' }} />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Button onClick={handleCheckCoverage} disabled={isBusy || isAggregateSelectionMode} variant="outline" className="w-full gap-2">
                {loadingCov ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                Check Coverage
              </Button>
              <div className="flex gap-2">
                <Button onClick={handleIngest} disabled={isBusy} className="min-w-0 flex-1 gap-2">
                  <DownloadCloud className="w-3.5 h-3.5" />
                  Ingest
                </Button>
                <Select value={exchange} onValueChange={(value) => setExchange(value as DatasetExchange)}>
                  <SelectTrigger className="w-36 shrink-0">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EXCHANGES.map((item) => (
                      <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button onClick={handleListTables} disabled={isBusy} variant="secondary" className="w-full gap-2">
                {loadingList ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5" />}
                List Tables
              </Button>
              <Button onClick={handleExportCsv} disabled={isBusy || isAggregateSelectionMode} variant="outline" className="w-full gap-2">
                {loadingExport
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Подготовка данных...</>
                  : <><Download className="w-3.5 h-3.5" /> Export CSV</>}
              </Button>
              <Button onClick={handleRepairDataset} disabled={isBusy || isAggregateSelectionMode || loadingQuality || loadingRepair || fixAllRunning} variant="outline" className="w-full gap-2">
                {loadingQuality
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <ShieldCheck className="w-3.5 h-3.5" />}
                Проверить целостность
              </Button>
              <Button onClick={handleDeleteRows} disabled={isBusy || isAggregateSelectionMode} variant="destructive" className="w-full gap-2">
                {loadingDelete ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                Очистить таблицу
              </Button>
              {isAggregateSelectionMode && (
                <p className="text-[11px] text-muted-foreground">
                  Режим <span className="font-mono">ALL</span> на symbol или exchange запускает ingest fan-out по набору конкретных dataset jobs. Coverage, export, repair и delete доступны только для конкретного symbol и конкретной биржи.
                </p>
              )}
            </div>
            {isMultiIngestMode && allIngestStatuses !== null && (
              <AllIngestProgress
                statuses={allIngestStatuses}
                meta={allIngestMeta}
                jobs={allJobs}
                jobIds={allIngestJobIds}
              />
            )}
            {timeframe !== 'ALL' && ingestStages !== null && <IngestProgress stages={ingestStages} />}
            {qualityProgress !== null && (() => {
              const { done, total, slots, errors, finished, errorLog } = qualityProgress;
              const pct = Math.round((done / total) * 100);
              return (
                <div className="rounded-lg border border-border bg-muted/30 px-3 py-2.5 space-y-2 relative">
                  {/* Close button — only when finished */}
                  {finished && (
                    <button
                      type="button"
                      onClick={() => setQualityProgress(null)}
                      className="absolute top-1.5 right-2 text-muted-foreground hover:text-foreground leading-none text-sm"
                      aria-label="Закрыть"
                    >
                      ×
                    </button>
                  )}
                  {/* Header: label + X/N counter */}
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-medium text-foreground flex items-center gap-1.5">
                      {finished
                        ? (errors > 0
                            ? <XCircle className="w-3 h-3 text-destructive" />
                            : <CheckCircle2 className="w-3 h-3 text-success" />)
                        : (loadingQuality
                            ? <Loader2 className="w-3 h-3 animate-spin text-primary" />
                            : <CheckCircle2 className="w-3 h-3 text-success" />)}
                      Аудит качества
                    </span>
                    <span className="text-[11px] font-semibold tabular-nums text-primary">
                      {done} / {total}
                    </span>
                  </div>
                  {/* Progress bar */}
                  <Progress value={pct} className={cn('h-2', errors > 0 && '[&>div]:bg-destructive')} />
                  {/* Active slots — hidden when finished */}
                  {!finished && (
                    <div className="flex flex-col gap-1">
                      {[slots[0] ?? null, slots[1] ?? null].map((slot, idx) => (
                        <div key={idx} className="flex flex-col">
                          <div className="flex items-center gap-2">
                            <div className="w-3.5 h-3.5 flex items-center justify-center shrink-0">
                              {slot === null ? (
                                <span className="text-[10px] text-muted-foreground/40 leading-none">—</span>
                              ) : slot.status === 'running' ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                              ) : slot.status === 'done' ? (
                                <CheckCircle2 className="w-3.5 h-3.5 text-success" />
                              ) : (
                                <XCircle className="w-3.5 h-3.5 text-destructive" />
                              )}
                            </div>
                            <span className={cn(
                              'text-[11px] font-mono w-10',
                              slot === null             ? 'text-muted-foreground/40' :
                              slot.status === 'error'   ? 'text-destructive'         :
                              slot.status === 'running' ? 'text-foreground'          :
                                                          'text-muted-foreground',
                            )}>
                              {slot?.tf ?? '—'}
                            </span>
                            {slot?.status === 'running' && slot.startedAt != null && (
                              <span className="text-[10px] text-muted-foreground tabular-nums">
                                {Math.floor((Date.now() - slot.startedAt) / 1000)}s
                              </span>
                            )}
                          </div>
                          {slot?.status === 'error' && (
                            <p className="text-[10px] text-destructive/80 pl-[22px] leading-tight line-clamp-1">
                              {formatErrorHint(slot.message ?? '')}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {/* Error log — shown when finished with errors */}
                  {finished && errorLog.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-[10px] font-medium text-destructive">Детали ошибок:</p>
                      <div className="max-h-24 overflow-y-auto flex flex-col gap-0.5">
                        {errorLog.map((e, i) => (
                          <p key={i} className="font-mono text-[10px] text-destructive/90 leading-tight">
                            {e.tf}  •  {formatErrorHint(e.message)}
                          </p>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}
          </CardContent>
        </Card>

        {/* Right — coverage result */}
        {isAggregateSelectionMode ? (
          <div className="hidden lg:flex items-center justify-center rounded-lg border border-dashed border-border h-44 px-4 text-center text-sm text-muted-foreground">
            Coverage для aggregate режима с ALL не считается как одна таблица. Используйте Ingest для постановки dataset jobs в очередь или выберите конкретный symbol и exchange для проверки покрытия.
          </div>
        ) : timeframe === 'ALL' && allCoverages !== null ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">Coverage: {symbol} — all timeframes</CardTitle>
            </CardHeader>
            <Separator />
            <CardContent className="p-0">
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timeframe</TableHead>
                    <TableHead className="text-right">Rows</TableHead>
                    <TableHead className="w-36">Coverage</TableHead>
                    <TableHead>From</TableHead>
                    <TableHead>To</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {allCoverages.map(row => (
                    <TableRow key={row.tf}>
                      <TableCell className="font-mono text-xs">{row.tf}</TableCell>
                      <TableCell className="text-xs text-right">{formatRows(row.rows, row.rows_known)}</TableCell>
                      <TableCell>
                        {row.coverage_pct == null ? (
                          <span className="text-xs text-muted-foreground">On demand</span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <Progress value={row.coverage_pct} className="h-1.5 flex-1" />
                            <span className={cn(
                              'text-xs w-10 text-right tabular-nums',
                              row.coverage_pct >= 95 ? 'text-success' :
                              row.coverage_pct >= 70 ? 'text-warning' : 'text-destructive',
                            )}>
                              {row.coverage_pct.toFixed(1)}%
                            </span>
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_from ?? '--'}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_to ?? '--'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              </div>
            </CardContent>
          </Card>
        ) : timeframe !== 'ALL' && coverage ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">Coverage: {symbol} {timeframe}</CardTitle>
            </CardHeader>
            <Separator />
            <CardContent className="p-3 sm:p-4 pt-4 space-y-4">
              <CoverageBar
                data={[{ name: `${symbol} ${timeframe}`, pct: coverage.coverage_pct ?? 0 }] satisfies BarDatum[]}
                height={100}
              />
              <div className="grid grid-cols-1 xs:grid-cols-3 gap-3 sm:gap-4">
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Rows</p>
                  <p className="text-lg font-bold">{formatRows(coverage.rows, coverage.rows_known)}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Expected</p>
                  <p className="text-lg font-bold">{coverage.expected?.toLocaleString() ?? '—'}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Gaps</p>
                  <p className="text-lg font-bold">{coverage.gaps?.toLocaleString() ?? '—'}</p>
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="hidden lg:flex items-center justify-center rounded-lg border border-dashed border-border h-44 text-sm text-muted-foreground">
            Run "Check Coverage" to see chart
          </div>
        )}
      </div>

      {/* ── Available Tables (full width) ── */}
      {tables === null && loadingList && (
        <Card>
          <CardContent className="pt-5 space-y-3">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </CardContent>
        </Card>
      )}
      {tables !== null && (
        <Card>
          <CardHeader className="pb-0">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold">Available Tables</CardTitle>
              <span className="text-xs text-muted-foreground">{tables.length} tables</span>
            </div>
          </CardHeader>
          <Separator className="mt-4" />
          <CardContent className="p-0">
            {tables.length === 0 ? (
              <div className="flex items-center justify-center py-12">
                <p className="text-sm text-muted-foreground">No tables found</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Table</TableHead>
                    <TableHead className="text-right">Rows</TableHead>
                    <TableHead className="w-44">Coverage</TableHead>
                    <TableHead>From</TableHead>
                    <TableHead>To</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tables.map(row => (
                    <TableRow key={row.table_name}>
                      <TableCell className="font-mono text-xs">{row.table_name}</TableCell>
                      <TableCell className="text-xs text-right">{formatRows(row.rows, row.rows_known)}</TableCell>
                      <TableCell>
                        {row.coverage_pct == null ? (
                          <span className="text-xs text-muted-foreground">On demand</span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <Progress value={row.coverage_pct} className="h-1.5 flex-1" />
                            <span className={cn(
                              'text-xs w-10 text-right tabular-nums',
                              row.coverage_pct >= 95 ? 'text-success' :
                              row.coverage_pct >= 70 ? 'text-warning' : 'text-destructive',
                            )}>
                              {row.coverage_pct.toFixed(1)}%
                            </span>
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_from ?? '--'}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_to ?? '--'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* ── Качество датасета (full width, открывается кнопкой «Repair Dataset») ── */}
      {(selectedTable !== null || allQualityResults !== null || loadingQuality) && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm font-semibold">
                {isAllMode
                  ? <>&#1050;&#1072;&#1095;&#1077;&#1089;&#1090;&#1074;&#1086; &#1076;&#1072;&#1090;&#1072;&#1089;&#1077;&#1090;&#1072;: <span className="font-mono">{symbol}</span> — все таймфреймы</>
                  : selectedTable
                    ? <>Качество датасета: <span className="font-mono">{selectedTable}</span></>
                    : 'Качество датасета'
                }
              </CardTitle>
              <div className="flex items-center gap-2 shrink-0">
                {isAllMode && allQualityResults !== null && (
                  <>
                    {fixAllRunning && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5 text-destructive border-destructive/50 hover:bg-destructive/10"
                        onClick={() => { fixAllCancelRef.current = true; }}
                      >
                        <XCircle className="w-3.5 h-3.5" />
                        Отменить
                      </Button>
                    )}
                    {!fixAllRunning && Object.values(allQualityResults).some(r => r.groups.some(g => g.status !== 'full')) && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5"
                        disabled={loadingQuality || loadingRepair}
                        onClick={() => void handleFixAll()}
                      >
                        <Wrench className="w-3.5 h-3.5" />
                        Исправить всё
                      </Button>
                    )}
                  </>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={fixAllRunning}
                  onClick={() => {
                    setSelectedTable(null);
                    setQualityReport(null);
                    setAllQualityResults(null);
                    setRepairStages(null);
                    setQualityProgress(null);
                    setFixAllProgress(null);
                    fixAllCancelRef.current = true;
                    setIsAllMode(false);
                  }}
                >
                  Закрыть
                </Button>
              </div>
            </div>
          </CardHeader>
          <Separator />
          <CardContent className="p-3 sm:p-4 space-y-3">
            {/* Loading skeleton */}
            {loadingQuality && !qualityReport && !allQualityResults && (
              <div className="space-y-2">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            )}

            {/* ── Single TF mode ── */}
            {!isAllMode && selectedTable && (
              <>
                {!qualityReport && !loadingQuality && (
                  <p className="text-xs text-muted-foreground">
                    Нажмите «Repair Dataset», чтобы оценить заполненность колонок датасета.
                  </p>
                )}
                {qualityReport && (
                  <>
                    <p className="text-xs text-muted-foreground">
                      Всего строк: {qualityReport.total_rows.toLocaleString()}
                    </p>
                    <div className="space-y-2">
                      {qualityReport.groups.map(g => {
                        const colorCls =
                          g.status === 'full'    ? 'text-success'    :
                          g.status === 'partial' ? 'text-warning'    :
                                                    'text-destructive';
                        const dotCls =
                          g.status === 'full'    ? 'bg-success'    :
                          g.status === 'partial' ? 'bg-warning'    :
                                                    'bg-destructive';
                        const needsRepair = g.status !== 'full';
                        const repairLabel =
                          g.repair_action === 'load_ohlcv'
                            ? 'Загрузить OHLCV'
                            : 'Пересчитать фичи';
                        return (
                          <div key={g.id} className="flex items-center gap-3 rounded-md border p-3">
                            <span className={cn('h-2.5 w-2.5 rounded-full shrink-0', dotCls)} />
                            <div className="flex-1 min-w-0">
                              <div className="text-sm font-medium truncate">{g.label}</div>
                              <div className="text-xs text-muted-foreground truncate">
                                {g.columns.length} колонок · {g.columns.join(', ')}
                              </div>
                            </div>
                            <div className="flex items-center gap-2 w-44">
                              <Progress value={g.fill_pct} className="h-1.5 flex-1" />
                              <span className={cn('text-xs w-12 text-right tabular-nums', colorCls)}>
                                {g.fill_pct.toFixed(1)}%
                              </span>
                            </div>
                            {needsRepair && (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={loadingRepair}
                                onClick={() => runRepair(selectedTable, g.repair_action)}
                              >
                                {loadingRepair && repairAction === g.repair_action
                                  ? <Loader2 className="h-3 w-3 animate-spin" />
                                  : <RefreshCw className="h-3 w-3" />}
                                <span className="ml-1.5">{repairLabel}</span>
                              </Button>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}
                {repairStages && (
                  <div className="rounded-md border p-3 space-y-2">
                    <div className="text-xs font-semibold text-muted-foreground">
                      {repairAction === 'load_ohlcv' ? 'Загрузка OHLCV' : 'Пересчёт фич'}
                    </div>
                    {repairStages.map(s => {
                      const Icon =
                        s.status === 'done'    ? CheckCircle2 :
                        s.status === 'error'   ? XCircle      :
                        s.status === 'running' ? Loader2      : Database;
                      const iconCls =
                        s.status === 'done'    ? 'text-success'     :
                        s.status === 'error'   ? 'text-destructive' :
                        s.status === 'running' ? 'animate-spin text-primary' :
                                                  'text-muted-foreground';
                      return (
                        <div key={s.id} className="flex items-center gap-2">
                          <Icon className={cn('h-3.5 w-3.5 shrink-0', iconCls)} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between text-xs">
                              <span className="truncate">{s.label}</span>
                              <span className="text-muted-foreground tabular-nums">
                                {s.detail ?? `${s.progress}%`}
                              </span>
                            </div>
                            <Progress value={s.progress} className="h-1 mt-1" />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            )}

            {/* ── ALL TF mode ── */}
            {isAllMode && allQualityResults && (
              <div className="space-y-2">
                {/* Fix All progress panel */}
                {fixAllProgress && (
                  <div className="rounded-lg border border-border bg-muted/30 px-3 py-2.5 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-medium flex items-center gap-1.5">
                        {fixAllProgress.done
                          ? (fixAllProgress.errors > 0
                              ? <XCircle className="w-3.5 h-3.5 text-destructive" />
                              : <CheckCircle2 className="w-3.5 h-3.5 text-success" />)
                          : <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />}
                        {fixAllProgress.done
                          ? `Готово: исправлено ${fixAllProgress.fixed}, ошибок ${fixAllProgress.errors}`
                          : `Исправление ${fixAllProgress.current} / ${fixAllProgress.total}`}
                      </span>
                      {fixAllProgress.done && (
                        <button
                          type="button"
                          onClick={() => setFixAllProgress(null)}
                          className="text-muted-foreground hover:text-foreground text-sm leading-none"
                          aria-label="Закрыть"
                        >×</button>
                      )}
                    </div>
                    {!fixAllProgress.done && (
                      <>
                        <Progress
                          value={fixAllProgress.total > 0 ? Math.round((fixAllProgress.current / fixAllProgress.total) * 100) : 0}
                          className="h-1.5"
                        />
                        {fixAllProgress.activeOps.length > 0 && (
                          <div className="flex flex-col gap-0.5">
                            {fixAllProgress.activeOps.map((op, i) => (
                              <div key={i} className="flex items-center gap-1.5">
                                <Loader2 className="w-2.5 h-2.5 animate-spin text-primary shrink-0" />
                                <p className="text-[11px] text-muted-foreground truncate">{op.label}</p>
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                    {fixAllProgress.done && fixAllProgress.completed.length > 0 && (
                      <div className="flex flex-col gap-0.5 max-h-28 overflow-y-auto">
                        {fixAllProgress.completed.map((c, i) => (
                          <div key={i} className="flex flex-col gap-0">
                            <div className="flex items-center gap-1.5 text-[10px] font-mono">
                              {c.ok
                                ? <CheckCircle2 className="w-3 h-3 text-success shrink-0" />
                                : <XCircle     className="w-3 h-3 text-destructive shrink-0" />}
                              <span className={c.ok ? 'text-muted-foreground' : 'text-destructive'}>{c.table}</span>
                              <span className="text-muted-foreground/60">·</span>
                              <span className="text-muted-foreground/80">
                                {c.action === 'load_ohlcv' ? 'OHLCV' : 'Пересчёт'}
                              </span>
                            </div>
                            {!c.ok && c.errorMessage && (
                              <p
                                className="text-[10px] text-destructive/80 pl-5 truncate"
                                title={c.errorMessage}
                              >{c.errorMessage}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {Object.entries(allQualityResults).map(([table, report]) => {
                  const isRepairing = selectedTable === table && loadingRepair;
                  return (
                    <div key={table} className="rounded-md border p-3 space-y-2">
                      <div className="flex items-center gap-3">
                        <span className="font-mono text-xs flex-1 min-w-0 truncate">{table}</span>
                        <div className="flex items-center gap-1.5">
                          {report.groups.map(g => {
                            const dotCls =
                              g.status === 'full'    ? 'bg-success'    :
                              g.status === 'partial' ? 'bg-warning'    :
                                                        'bg-destructive';
                            return (
                              <span
                                key={g.id}
                                title={g.label}
                                className={cn('h-2 w-2 rounded-full shrink-0', dotCls)}
                              />
                            );
                          })}
                        </div>
                        <span className="text-xs text-muted-foreground tabular-nums w-20 text-right shrink-0">
                          {report.total_rows.toLocaleString()} rows
                        </span>
                      </div>
                      {report.groups.some(g => g.status !== 'full') && (
                        <div className="flex flex-wrap gap-2">
                          {report.groups.filter(g => g.status !== 'full').map(g => {
                            const repairLabel =
                              g.repair_action === 'load_ohlcv'
                                ? 'Загрузить OHLCV'
                                : 'Пересчитать фичи';
                            return (
                              <Button
                                key={g.id}
                                size="sm"
                                variant="outline"
                                disabled={loadingRepair || fixAllRunning}
                                onClick={() => {
                                  setSelectedTable(table);
                                  void runRepair(table, g.repair_action);
                                }}
                              >
                                {isRepairing && repairAction === g.repair_action
                                  ? <Loader2 className="h-3 w-3 animate-spin" />
                                  : <RefreshCw className="h-3 w-3" />}
                                <span className="ml-1.5">{g.label}: {repairLabel}</span>
                              </Button>
                            );
                          })}
                        </div>
                      )}
                      {selectedTable === table && repairStages && (
                        <div className="rounded-md bg-muted/30 p-2 space-y-1.5">
                          <div className="text-xs font-semibold text-muted-foreground">
                            {repairAction === 'load_ohlcv' ? 'Загрузка OHLCV' : 'Пересчёт фич'}
                          </div>
                          {repairStages.map(s => {
                            const Icon =
                              s.status === 'done'    ? CheckCircle2 :
                              s.status === 'error'   ? XCircle      :
                              s.status === 'running' ? Loader2      : Database;
                            const iconCls =
                              s.status === 'done'    ? 'text-success'     :
                              s.status === 'error'   ? 'text-destructive' :
                              s.status === 'running' ? 'animate-spin text-primary' :
                                                        'text-muted-foreground';
                            return (
                              <div key={s.id} className="flex items-center gap-2">
                                <Icon className={cn('h-3.5 w-3.5 shrink-0', iconCls)} />
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center justify-between text-xs">
                                    <span className="truncate">{s.label}</span>
                                    <span className="text-muted-foreground tabular-nums">
                                      {s.detail ?? `${s.progress}%`}
                                    </span>
                                  </div>
                                  <Progress value={s.progress} className="h-1 mt-1" />
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* ── Action history (full width) ── */}
      <Card>
        <CardHeader className="pb-0">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold">Action History</CardTitle>
            <span className="text-xs text-muted-foreground">Last 20</span>
          </div>
        </CardHeader>
        <Separator className="mt-4" />
        <CardContent className="p-0">
          {datasetHistory.length === 0 ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-muted-foreground">No actions yet</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Params</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead className="text-right">ms</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {datasetHistory.map(h => (
                  <TableRow key={h.id}>
                    <TableCell className="font-mono text-xs">{h.time}</TableCell>
                    <TableCell>
                      <Badge variant={h.action === 'Download' ? 'success' : 'info'} className="text-xs">
                        {h.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {h.params.symbol} {h.params.timeframe}{h.params.exchange ? ` · ${h.params.exchange}` : ''}
                    </TableCell>
                    <TableCell className="text-xs max-w-xs truncate">{h.result}</TableCell>
                    <TableCell className="text-xs text-right text-muted-foreground">{h.durationMs}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
