-- Open-Write Web Platform — PostgreSQL Schema
-- Generated from the live database.
-- Apply with: psql "$DATABASE_URL" -f schema.sql

-- Required extension for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
    account_tier  TEXT NOT NULL DEFAULT 'basic',
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Add account_tier to existing users tables (idempotent).
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS account_tier TEXT NOT NULL DEFAULT 'basic';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- ── Token Usage Tracking ─────────────────────────────────────────────────────
-- Tracks per-user token consumption in 30-day rolling billing periods.
-- The settings_store.record_token_usage() function creates period rows and
-- increments usage.  get_token_usage() reads the current period.
CREATE TABLE IF NOT EXISTS token_usage (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
    tokens_used   INTEGER NOT NULL DEFAULT 0,
    period_start  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_end    TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 days',
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_token_usage_user_period
    ON token_usage (user_id, period_start, period_end);

-- ── Projects ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    format      TEXT DEFAULT 'novel',
    source_path TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- ── User Settings ─────────────────────────────────────────────────────────────
-- Stores per-user JSON config (API keys, model routing, defaults).
-- One row per user (enforced by the UNIQUE constraint on user_id).
CREATE TABLE IF NOT EXISTS user_settings (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    settings_json TEXT NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMPTZ DEFAULT now()
);

-- ── Versions ─────────────────────────────────────────────────────────────────
-- One row per phase artifact snapshot (bible, chapter prose, critic report, etc.).
-- Captured automatically after each pipeline phase completes.
CREATE TABLE IF NOT EXISTS versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    user_id        UUID REFERENCES users(id) ON DELETE CASCADE,
    phase          TEXT NOT NULL,           -- e.g. "bible", "writer", "critics"
    chapter_number INTEGER,                 -- NULL for project-scope phases
    content_type   TEXT NOT NULL,           -- e.g. "prose", "outline", "critic_report"
    content        TEXT NOT NULL,
    word_count     INTEGER,
    critic_verdict TEXT,                    -- "PASS" | "REVISE" | NULL
    metadata_json  TEXT DEFAULT '{}',
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_versions_project
    ON versions (project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_versions_chapter
    ON versions (project_id, chapter_number, content_type);

-- ── Approved Emails (beta gating) ─────────────────────────────────────────────
-- Controls who can create accounts during the beta period.
-- Checked before signup; independent of the users table.
CREATE TABLE IF NOT EXISTS approved_emails (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email      TEXT NOT NULL UNIQUE,
    is_admin   BOOLEAN NOT NULL DEFAULT FALSE,
    added_by   TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Seed the primary admin account.
INSERT INTO approved_emails (email, is_admin)
VALUES ('detweiler.nicholas@gmail.com', TRUE)
ON CONFLICT (email) DO NOTHING;

-- ── Editorial Reviews ─────────────────────────────────────────────────────────
-- Stores user-uploaded work for editorial review (not tied to pipeline projects).
CREATE TABLE IF NOT EXISTS editorial_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'Untitled',
    original_content TEXT NOT NULL,
    current_content TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'prose',
    supporting_materials TEXT DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS editorial_review_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id UUID REFERENCES editorial_reviews(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    feedback TEXT DEFAULT '',
    instructions TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS editorial_review_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id UUID REFERENCES editorial_reviews(id) ON DELETE CASCADE,
    report_type TEXT NOT NULL,
    report TEXT NOT NULL,
    verdict TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_editorial_reviews_user
    ON editorial_reviews (user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_editorial_review_versions
    ON editorial_review_versions (review_id, version_number DESC);

CREATE INDEX IF NOT EXISTS idx_editorial_review_reports
    ON editorial_review_reports (review_id, report_type);

-- ── Custom Adversarial Reader Personas ──────────────────────────────────────
-- Stores user-defined reader personas for the editorial review system.
-- Each persona is a validated JSON spec that defines who reads, what they
-- evaluate, what they ignore, and how they structure their output.
CREATE TABLE IF NOT EXISTS editorial_personas (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
    persona_json  TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_personas_user
    ON editorial_personas (user_id, updated_at DESC);

-- ── Editorial Review Runs (per-persona executions against a review) ─────────
-- Tracks each execution of a persona against a review, storing the assembled
-- prompt for cache analysis and the rendered output.
CREATE TABLE IF NOT EXISTS editorial_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id     UUID REFERENCES editorial_reviews(id) ON DELETE CASCADE,
    user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
    persona_id    UUID REFERENCES editorial_personas(id) ON DELETE SET NULL,
    persona_name  TEXT NOT NULL DEFAULT '',
    rubric_json   TEXT,
    output        TEXT NOT NULL DEFAULT '',
    severity      INTEGER DEFAULT 3,
    cache_hit_tokens  INTEGER DEFAULT 0,
    cache_miss_tokens INTEGER DEFAULT 0,
    cost_usd      NUMERIC(10, 6) DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_editorial_runs_review
    ON editorial_runs (review_id, created_at DESC);
