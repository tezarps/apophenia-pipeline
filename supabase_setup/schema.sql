-- Narava pipeline — Supabase schema
-- Run this in Supabase Studio (SQL Editor) once the project exists.
-- Mirrors topics/mythology_topics.csv so it can be the live source of truth
-- once GitHub Actions runs the pipeline instead of the local Mac.

create table if not exists topics (
    id bigint primary key,
    category text not null,
    topic text not null,
    angle text not null,
    status text not null default 'pending' check (status in ('pending', 'published', 'failed', 'awaiting_images')),
    video_id text default '',
    notes text default ''
);

-- Pipeline run log — lets the dashboard show live progress per agent stage
-- (oracle/scribe/voice/architect/herald/messenger), replacing whatever local
-- status_manager.py currently tracks.
create table if not exists pipeline_runs (
    id bigserial primary key,
    topic_id bigint references topics(id),
    angle text,
    status text not null default 'running' check (status in ('running', 'done', 'failed', 'paused')),
    current_agent text,
    error text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists idx_pipeline_runs_topic_id on pipeline_runs(topic_id);
create index if not exists idx_topics_status on topics(status);

-- Row Level Security: service_role (used by GitHub Actions + scripts) bypasses
-- RLS by default, so no policies are strictly required for the pipeline itself.
-- Enable RLS so the anon key (used by the public Vercel dashboard) can only
-- read, never write.
alter table topics enable row level security;
alter table pipeline_runs enable row level security;

create policy "public read topics" on topics
    for select using (true);

create policy "public read pipeline_runs" on pipeline_runs
    for select using (true);

-- Thumbnail A/B self-test — since YouTube's native "Test & Compare" has no
-- public API, this tracks our own sequential rotation: publish with A, swap
-- to B after a few days, compare CTR, keep the winner. Driven by
-- ab_test_check.py on a daily GitHub Actions cron.
create table if not exists thumbnail_tests (
    id bigserial primary key,
    topic_id bigint references topics(id),
    video_id text not null,
    active_variant text not null default 'A' check (active_variant in ('A', 'B')),
    started_at timestamptz not null default now(),
    switched_at timestamptz,
    resolved boolean not null default false,
    ctr_a numeric,
    ctr_b numeric,
    winner text check (winner in ('A', 'B'))
);

create index if not exists idx_thumbnail_tests_resolved on thumbnail_tests(resolved);

alter table thumbnail_tests enable row level security;

create policy "public read thumbnail_tests" on thumbnail_tests
    for select using (true);

-- After running this file, migrate existing CSV rows with the companion
-- script: python3 supabase_setup/migrate_csv_to_supabase.py
