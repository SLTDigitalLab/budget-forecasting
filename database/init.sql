-- System-wide forecast records. User ownership may be added later after authentication approval.
-- Backend also creates these tables on startup for existing database volumes.

CREATE TABLE IF NOT EXISTS forecast_runs (
    id BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    category TEXT NOT NULL,
    overall_best_algorithm TEXT NOT NULL,
    forecast_type TEXT NOT NULL,
    requested_start_month TEXT NOT NULL,
    requested_end_month TEXT NOT NULL,
    forecast_month_count INTEGER NOT NULL,
    selected_account_count INTEGER NOT NULL,
    amount_unit TEXT NOT NULL,
    overall_total DOUBLE PRECISION NOT NULL,
    monthly_average DOUBLE PRECISION NOT NULL,
    minimum_monthly_forecast DOUBLE PRECISION NOT NULL,
    maximum_monthly_forecast DOUBLE PRECISION NOT NULL,
    history_end TEXT,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast_monthly_results (
    id BIGSERIAL PRIMARY KEY,
    forecast_run_id BIGINT NOT NULL REFERENCES forecast_runs(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    forecast_amount DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS forecast_runs_generated_at_idx
    ON forecast_runs (generated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS forecast_monthly_results_run_month_idx
    ON forecast_monthly_results (forecast_run_id, month);

CREATE TABLE IF NOT EXISTS dataset_files (
    id UUID PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    content_type TEXT,
    file_size INTEGER NOT NULL DEFAULT 0,
    checksum TEXT NOT NULL,
    sheet_name TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    staged_path TEXT,
    is_legacy BOOLEAN NOT NULL DEFAULT FALSE,
    uploaded_at TIMESTAMPTZ NOT NULL,
    uploaded_tid TEXT NOT NULL,
    uploaded_oid TEXT NOT NULL,
    uploaded_display_name TEXT,
    uploaded_username TEXT,
    last_edited_at TIMESTAMPTZ,
    last_edited_tid TEXT,
    last_edited_oid TEXT,
    last_edited_display_name TEXT,
    last_edited_username TEXT
);

CREATE TABLE IF NOT EXISTS historical_actuals (
    id BIGSERIAL PRIMARY KEY,
    budget_code TEXT NOT NULL,
    month TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    description TEXT,
    category TEXT,
    source_file_id UUID REFERENCES dataset_files(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (budget_code, month)
);

CREATE TABLE IF NOT EXISTS dataset_previews (
    id UUID PRIMARY KEY,
    owner_tid TEXT NOT NULL,
    owner_oid TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_filename TEXT,
    sheet_name TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_audit_events (
    id BIGSERIAL PRIMARY KEY,
    file_id UUID REFERENCES dataset_files(id) ON DELETE SET NULL,
    file_name TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_tid TEXT NOT NULL,
    actor_oid TEXT NOT NULL,
    actor_display_name TEXT,
    actor_username TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    changed_count INTEGER NOT NULL DEFAULT 0,
    changes JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS historical_actuals_month_idx ON historical_actuals (month);
CREATE INDEX IF NOT EXISTS dataset_files_uploaded_at_idx ON dataset_files (uploaded_at DESC);
CREATE INDEX IF NOT EXISTS dataset_previews_owner_idx ON dataset_previews (owner_tid, owner_oid, expires_at);

ALTER TABLE dataset_files ADD COLUMN IF NOT EXISTS validation_summary JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE dataset_files ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
ALTER TABLE dataset_files ADD COLUMN IF NOT EXISTS confirmed_display_name TEXT;
ALTER TABLE dataset_files ADD COLUMN IF NOT EXISTS confirmed_username TEXT;
ALTER TABLE dataset_files ADD COLUMN IF NOT EXISTS rollback_reason TEXT;
ALTER TABLE dataset_files ADD COLUMN IF NOT EXISTS rolled_back_at TIMESTAMPTZ;
ALTER TABLE dataset_files ADD COLUMN IF NOT EXISTS change_kind TEXT NOT NULL DEFAULT 'UPLOAD';
ALTER TABLE dataset_audit_events ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE dataset_audit_events ADD COLUMN IF NOT EXISTS summary JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS dataset_master_meta (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    earliest_month TEXT,
    latest_month TEXT,
    budget_codes JSONB NOT NULL DEFAULT '[]'::jsonb
);

INSERT INTO dataset_master_meta (id, version)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE dataset_master_meta ADD COLUMN IF NOT EXISTS earliest_month TEXT;
ALTER TABLE dataset_master_meta ADD COLUMN IF NOT EXISTS latest_month TEXT;
ALTER TABLE dataset_master_meta ADD COLUMN IF NOT EXISTS budget_codes JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS historical_change_records (
    id BIGSERIAL PRIMARY KEY,
    audit_event_id BIGINT,
    file_id UUID,
    action TEXT NOT NULL,
    operation TEXT NOT NULL,
    budget_code TEXT NOT NULL,
    month TEXT NOT NULL,
    previous_amount DOUBLE PRECISION,
    new_amount DOUBLE PRECISION,
    previous_category TEXT,
    new_category TEXT,
    previous_description TEXT,
    new_description TEXT,
    actor_display_name TEXT,
    actor_username TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    reversed_by_audit_id BIGINT
);

CREATE INDEX IF NOT EXISTS historical_change_records_key_idx
    ON historical_change_records (budget_code, month, id);

CREATE TABLE IF NOT EXISTS system_coordination (
    id INTEGER PRIMARY KEY,
    retrain_job_id TEXT,
    active_operations INTEGER NOT NULL DEFAULT 0,
    operations JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE system_coordination ADD COLUMN IF NOT EXISTS operations JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO system_coordination (id, active_operations)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS retrain_jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    initiator_display_name TEXT,
    initiator_username TEXT,
    manage_token_hash TEXT NOT NULL,
    snapshot_revision INTEGER,
    snapshot_fingerprint TEXT,
    snapshot_source TEXT,
    snapshot_path TEXT,
    earliest_month TEXT,
    latest_month TEXT,
    budget_code_count INTEGER,
    candidate_path TEXT,
    candidate_sha256 TEXT,
    candidate_validated BOOLEAN NOT NULL DEFAULT FALSE,
    progress_stage TEXT,
    progress_current INTEGER,
    progress_total INTEGER,
    logs TEXT NOT NULL DEFAULT '',
    error_detail TEXT,
    error_code TEXT,
    publication_status TEXT NOT NULL DEFAULT 'not_published',
    cache_warning TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    unlock_at TIMESTAMPTZ,
    abandon_at TIMESTAMPTZ,
    worker_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE retrain_jobs ADD COLUMN IF NOT EXISTS initiator_tid TEXT;
ALTER TABLE retrain_jobs ADD COLUMN IF NOT EXISTS initiator_oid TEXT;
ALTER TABLE retrain_jobs ADD COLUMN IF NOT EXISTS initiator_identity_source TEXT;
ALTER TABLE retrain_jobs ADD COLUMN IF NOT EXISTS progress_budget_code TEXT;

CREATE TABLE IF NOT EXISTS settings_action_audit (
    id TEXT PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    actor_tid TEXT NOT NULL,
    actor_oid TEXT NOT NULL,
    actor_display_name TEXT,
    actor_email TEXT,
    job_id TEXT,
    revision_id TEXT,
    actor_identity_source TEXT
);

ALTER TABLE settings_action_audit ADD COLUMN IF NOT EXISTS actor_identity_source TEXT;

CREATE TABLE IF NOT EXISTS model_publication_meta (
    id INTEGER PRIMARY KEY,
    published_at TIMESTAMPTZ,
    snapshot_fingerprint TEXT,
    snapshot_revision INTEGER,
    job_id TEXT,
    publisher_tid TEXT,
    publisher_oid TEXT
);
