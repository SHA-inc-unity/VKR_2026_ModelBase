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
import type { TrainProgressEvent, ModelReadyEvent, IngestProgressEvent } from '@/lib/types';

// Reverse lookup: topic value → Topics key (built once at module level)
const TOPIC_VALUE_TO_KEY = Object.fromEntries(
  (Object.entries(Topics) as [string, string][]).map(([k, v]) => [v, k]),
) as Record<string, string>;

// Payload type map for EVT_ topics
interface EventPayloadMap {
  EVT_ANALYTICS_TRAIN_PROGRESS: TrainProgressEvent;
  EVT_ANALYTICS_MODEL_READY: ModelReadyEvent;
  EVT_DATA_INGEST_PROGRESS: IngestProgressEvent;
}

export type EventHandlers = Partial<{
  [K in keyof EventPayloadMap]: (payload: EventPayloadMap[K]) => void;
}>;

interface SseMessage {
  type: string;
  payload: unknown;
}

export function useEvents(handlers: EventHandlers): void {
  // Keep the latest handlers without triggering reconnect
  const handlersRef = useRef<EventHandlers>(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
    const es = new EventSource(`${base}/api/events`);

    es.onmessage = (event: MessageEvent<string>) => {
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
      }
    };

    es.onerror = () => {
      // EventSource reconnects automatically; no action needed
    };

    return () => {
      es.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // open once on mount, close on unmount
}
