'use client';
/**
 * useEvents — subscribes to the /api/events SSE stream and dispatches
 * incoming Kafka event messages to matching handler callbacks.
 *
 * Usage:
 *   useEvents({
 *     EVT_ANALYTICS_MODEL_READY: (payload) => { ... },
 *   });
 *
 * The EventSource is opened on mount and closed on unmount.
 * Handlers are kept in a ref so callers can pass inline arrow functions
 * without triggering a reconnect.
 */
import { useEffect, useRef } from 'react';
import { Topics } from '@/lib/topics';
import type {
  TrainProgressEvent,
  ModelReadyEvent,
  IngestProgressEvent,
  RepairProgressEvent,
  DatasetJobProgressEvent,
  DatasetJobCompletedEvent,
} from '@/lib/types';

// Reverse lookup: topic value → Topics key (built once at module level)
const TOPIC_VALUE_TO_KEY = Object.fromEntries(
  (Object.entries(Topics) as [string, string][]).map(([k, v]) => [v, k]),
) as Record<string, string>;

// Payload type map for EVT_ topics
interface EventPayloadMap {
  EVT_ANALYTICS_TRAIN_PROGRESS: TrainProgressEvent;
  EVT_ANALYTICS_MODEL_READY: ModelReadyEvent;
  EVT_DATA_INGEST_PROGRESS: IngestProgressEvent;
  EVT_ANALITIC_DATASET_REPAIR_PROGRESS: RepairProgressEvent;
  EVT_DATA_DATASET_JOB_PROGRESS: DatasetJobProgressEvent;
  EVT_DATA_DATASET_JOB_COMPLETED: DatasetJobCompletedEvent;
}

export type EventHandlers = Partial<{
  [K in keyof EventPayloadMap]: (payload: EventPayloadMap[K]) => void;
}>;

interface SseMessage {
  type: string;
  payload: unknown;
}

export interface UseEventsOptions {
  /** Notified on connect (true) / disconnect (false) so a UI can surface a
   *  "live feed reconnecting" indicator. */
  onConnectionChange?: (connected: boolean) => void;
}

export function useEvents(handlers: EventHandlers, options?: UseEventsOptions): void {
  // Keep the latest handlers/options without triggering reconnect
  const handlersRef = useRef<EventHandlers>(handlers);
  handlersRef.current = handlers;
  const optionsRef = useRef<UseEventsOptions | undefined>(options);
  optionsRef.current = options;

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closedByUs = false;
    let backoffMs = 1_000;

    const dispatch = (event: MessageEvent<string>) => {
      let msg: SseMessage;
      try {
        msg = JSON.parse(event.data) as SseMessage;
      } catch {
        return;
      }

      const topicKey = TOPIC_VALUE_TO_KEY[msg.type];
      if (!topicKey) return;

      const h = handlersRef.current;

      // Explicit switch keeps TypeScript happy without casting to `any`
      switch (topicKey) {
        case 'EVT_ANALYTICS_TRAIN_PROGRESS':
          h.EVT_ANALYTICS_TRAIN_PROGRESS?.(msg.payload as TrainProgressEvent);
          break;
        case 'EVT_ANALYTICS_MODEL_READY':
          h.EVT_ANALYTICS_MODEL_READY?.(msg.payload as ModelReadyEvent);
          break;
        case 'EVT_DATA_INGEST_PROGRESS':
          h.EVT_DATA_INGEST_PROGRESS?.(msg.payload as IngestProgressEvent);
          break;
        case 'EVT_ANALITIC_DATASET_REPAIR_PROGRESS':
          h.EVT_ANALITIC_DATASET_REPAIR_PROGRESS?.(msg.payload as RepairProgressEvent);
          break;
        case 'EVT_DATA_DATASET_JOB_PROGRESS':
          h.EVT_DATA_DATASET_JOB_PROGRESS?.(msg.payload as DatasetJobProgressEvent);
          break;
        case 'EVT_DATA_DATASET_JOB_COMPLETED':
          h.EVT_DATA_DATASET_JOB_COMPLETED?.(msg.payload as DatasetJobCompletedEvent);
          break;
      }
    };

    const connect = () => {
      es = new EventSource(`${base}/api/events`);
      es.onopen = () => {
        backoffMs = 1_000;
        optionsRef.current?.onConnectionChange?.(true);
      };
      es.onmessage = dispatch;
      es.onerror = () => {
        // Not silent: surface the disconnect so a stale feed is visible.
        optionsRef.current?.onConnectionChange?.(false);
        // If the browser gave up (CLOSED), recreate with capped backoff rather
        // than leaving a permanently dead stream.
        if (es && es.readyState === EventSource.CLOSED && !closedByUs) {
          es.close();
          if (reconnectTimer) clearTimeout(reconnectTimer);
          reconnectTimer = setTimeout(connect, backoffMs);
          backoffMs = Math.min(backoffMs * 2, 30_000);
        }
      };
    };

    connect();

    return () => {
      closedByUs = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // open once on mount, close on unmount
}
