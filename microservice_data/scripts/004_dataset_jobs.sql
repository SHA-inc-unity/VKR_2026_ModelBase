-- Migration 004: dataset background-job tracking tables.
--
-- Phase A of the Dataset jobs redesign. These tables are the single source
-- of truth (within microservice_data) for long-running dataset operations:
-- ingest, detect_anomalies, compute_features, clean.apply, export,
-- import_csv, upsert_ohlcv. Each operation gets a row in dataset_jobs;
-- ALL-mode runs split into per-table rows in dataset_job_subtasks; per-stage
-- timing is captured in dataset_job_stages.
--
-- The tables are created idempotently on service startup by
-- DatasetJobsRepository.EnsureSchemaAsync(); this script is kept for ops
-- folks who want to apply the schema out-of-band.

CREATE TABLE IF NOT EXISTS dataset_jobs (
    job_id            UUID         PRIMARY KEY,
    type              TEXT         NOT NULL,
    conflict_class    TEXT         NOT NULL,
    target_table      TEXT         NULL,
    target_symbol     TEXT         NULL,
    target_timeframe  TEXT         NULL,
    target_start_ms   BIGINT       NULL,
    target_end_ms     BIGINT       NULL,
    params_json       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    params_hash       TEXT         NOT NULL,
    status            TEXT         NOT NULL,        -- queued|running|succeeded|failed|canceled
    stage             TEXT         NULL,
    progress          SMALLINT     NOT NULL DEFAULT 0,
    detail            TEXT         NULL,
    total             BIGINT       NOT NULL DEFAULT 0,
    completed         BIGINT       NOT NULL DEFAULT 0,
    failed            BIGINT       NOT NULL DEFAULT 0,
    skipped           BIGINT       NOT NULL DEFAULT 0,
    error_code        TEXT         NULL,
    error_message     TEXT         NULL,
    cancel_requested  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_by        TEXT         NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ  NULL,
    finished_at       TIMESTAMPTZ  NULL
);

CREATE INDEX IF NOT EXISTS idx_dataset_jobs_status_type
    ON dataset_jobs (status, type);
CREATE INDEX IF NOT EXISTS idx_dataset_jobs_target_table
    ON dataset_jobs (target_table);
CREATE INDEX IF NOT EXISTS idx_dataset_jobs_created_at
    ON dataset_jobs (created_at DESC);

-- Active-dedup: same params_hash cannot have two simultaneously
-- queued-or-running jobs. Partial unique index makes the check race-free.
CREATE UNIQUE INDEX IF NOT EXISTS uq_dataset_jobs_active_params
    ON dataset_jobs (params_hash)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS dataset_job_subtasks (
    subtask_id      UUID         PRIMARY KEY,
    job_id          UUID         NOT NULL REFERENCES dataset_jobs(job_id) ON DELETE CASCADE,
    idx             INT          NOT NULL,
    label           TEXT         NOT NULL,
    target_table    TEXT         NULL,
    status          TEXT         NOT NULL,    -- queued|running|succeeded|failed|canceled|skipped
    progress        SMALLINT     NOT NULL DEFAULT 0,
    detail          TEXT         NULL,
    total           BIGINT       NOT NULL DEFAULT 0,
    completed       BIGINT       NOT NULL DEFAULT 0,
    failed          BIGINT       NOT NULL DEFAULT 0,
    skipped         BIGINT       NOT NULL DEFAULT 0,
    error_code      TEXT         NULL,
    error_message   TEXT         NULL,
    started_at      TIMESTAMPTZ  NULL,
    finished_at     TIMESTAMPTZ  NULL,
    UNIQUE (job_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_dataset_job_subtasks_job
    ON dataset_job_subtasks (job_id);

CREATE TABLE IF NOT EXISTS dataset_job_stages (
    stage_id         UUID         PRIMARY KEY,
    job_id           UUID         NOT NULL REFERENCES dataset_jobs(job_id) ON DELETE CASCADE,
    subtask_id       UUID         NULL REFERENCES dataset_job_subtasks(subtask_id) ON DELETE CASCADE,
    name             TEXT         NOT NULL,
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ended_at         TIMESTAMPTZ  NULL,
    items_processed  BIGINT       NULL,
    custom_metrics   JSONB        NULL
);

CREATE INDEX IF NOT EXISTS idx_dataset_job_stages_job
    ON dataset_job_stages (job_id);
