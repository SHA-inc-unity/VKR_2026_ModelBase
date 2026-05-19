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
    const refreshNow = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void refreshActiveJobs();
    };

    refreshNow();
    if (pollActiveMs <= 0) return;

    const timer = window.setInterval(() => {
      refreshNow();
    }, pollActiveMs);

    const handleVisible = () => {
      refreshNow();
    };

    window.addEventListener('focus', handleVisible);
    document.addEventListener('visibilitychange', handleVisible);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', handleVisible);
      document.removeEventListener('visibilitychange', handleVisible);
    };
  }, [pollActiveMs]);
}