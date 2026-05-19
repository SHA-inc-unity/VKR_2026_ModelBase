'use client';

import { useEffect } from 'react';
import { useEvents } from '@/hooks/useEvents';
import { applyJobCompleted, applyJobProgress, refreshActiveJobs } from '@/hooks/useDatasetJobs';

export function useDatasetJobsFeed(pollActiveMs = 0): void {
  useEvents({
    EVT_DATA_DATASET_JOB_PROGRESS: (event) => applyJobProgress(event),
    EVT_DATA_DATASET_JOB_COMPLETED: (event) => applyJobCompleted(event),
  });

  useEffect(() => {
    void refreshActiveJobs();
    if (pollActiveMs <= 0) return;

    const timer = window.setInterval(() => {
      void refreshActiveJobs();
    }, pollActiveMs);

    return () => {
      window.clearInterval(timer);
    };
  }, [pollActiveMs]);
}