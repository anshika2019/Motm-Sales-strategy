-- ============================================================================
-- MOTM AI Sales Director — knowledge base + chat schema (reference copy)
--
-- This is a read-only mirror of migrations/versions/894de2b8cebd_add_knowledge_base_and_chat_schema.py
-- for quick reading, or for pasting into the Supabase SQL editor if you want
-- to eyeball/apply the shape by hand. The Alembic migration is the source of
-- truth — if the two ever disagree, the migration wins. This file is not
-- itself run by Alembic and isn't tracked in supabase/migrations/ (that
-- folder is a frozen historical record of the pre-Alembic schema — see
-- README.md).
--
-- Verified 2026-08-14 by actually applying the Alembic migration (upgrade
-- and downgrade) against the project's dev database and diffing the result
-- against app/db/models.py via `alembic check` (clean).
-- ============================================================================

create extension if not exists vector;

-- ----------------------------------------------------------------------------
-- Enums
-- ----------------------------------------------------------------------------

-- conversations.persona: which side of the app a chat session belongs to.
create type persona as enum (
  'sell_motm',
  'support_customer'
);

-- knowledge_entries.persona: superset of `persona` -- 'shared' entries are
-- surfaced to both sides.
create type knowledge_persona as enum (
  'sell_motm',
  'support_customer',
  'shared'
);

create type knowledge_type as enum (
  'principle_card',
  'case_card',
  'objection',
  'positioning',
  'pricing',
  'other'
);

create type message_sender as enum (
  'user',
  'assistant'
);

create type feedback_rating as enum (
  'useful',
  'not_useful'
);

-- ----------------------------------------------------------------------------
-- knowledge_entries: the searchable knowledge base for both personas. One
-- flexible table rather than split per document type, since everything is
-- searched together via embedding similarity. `embedding` is nullable --
-- it's populated by an async embedding job after the row is written, not
-- atomically with it. `created_by` is ON DELETE SET NULL (not CASCADE):
-- deleting an employee's account shouldn't delete the company knowledge
-- base content they authored.
-- ----------------------------------------------------------------------------
create table public.knowledge_entries (
  id uuid primary key default gen_random_uuid(),
  persona knowledge_persona not null,
  type knowledge_type not null,
  title text not null,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding vector(1024),
  is_active boolean not null default true,
  created_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index knowledge_entries_persona_idx on public.knowledge_entries (persona);
create index knowledge_entries_type_idx on public.knowledge_entries (type);

-- HNSW + cosine distance: Cohere's embed models are compared via cosine
-- similarity. Query with `order by embedding <=> $1 limit n`.
create index knowledge_entries_embedding_hnsw_idx
  on public.knowledge_entries using hnsw (embedding vector_cosine_ops);

-- ----------------------------------------------------------------------------
-- conversations: one chat session between a user and the AI.
-- ----------------------------------------------------------------------------
create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  persona persona not null,
  title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index conversations_user_id_idx on public.conversations (user_id);

-- ----------------------------------------------------------------------------
-- messages: every message inside a conversation.
-- ----------------------------------------------------------------------------
create table public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations (id) on delete cascade,
  sender message_sender not null,
  content text not null,
  pipeline_metadata jsonb,
  created_at timestamptz not null default now()
);

create index messages_conversation_id_idx on public.messages (conversation_id);

-- ----------------------------------------------------------------------------
-- message_sources: join table linking an AI answer to the knowledge entries
-- it cited. Both FKs cascade -- a citation record is meaningless once
-- either side is gone.
-- ----------------------------------------------------------------------------
create table public.message_sources (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references public.messages (id) on delete cascade,
  knowledge_entry_id uuid not null references public.knowledge_entries (id) on delete cascade,
  relevance_score float
);

create index message_sources_message_id_idx on public.message_sources (message_id);
create index message_sources_knowledge_entry_id_idx on public.message_sources (knowledge_entry_id);

-- ----------------------------------------------------------------------------
-- feedback: Useful / Not Useful rating on an AI message.
-- ----------------------------------------------------------------------------
create table public.feedback (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references public.messages (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  rating feedback_rating not null,
  comment text,
  created_at timestamptz not null default now()
);

create index feedback_message_id_idx on public.feedback (message_id);
create index feedback_user_id_idx on public.feedback (user_id);

-- ----------------------------------------------------------------------------
-- website_analyses: output of the website analyzer tool (Support Customer
-- persona only).
-- ----------------------------------------------------------------------------
create table public.website_analyses (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations (id) on delete cascade,
  url text not null,
  analysis_result jsonb not null,
  created_at timestamptz not null default now()
);

create index website_analyses_conversation_id_idx on public.website_analyses (conversation_id);
