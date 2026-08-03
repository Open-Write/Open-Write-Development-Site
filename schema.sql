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
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ── Projects ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    format      TEXT DEFAULT 'novel',
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
