-- PostgreSQL + pgvector schema for the future multi-user, multi-book RPG system.
-- Current local JSON saves remain supported; this schema is the migration target.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS app_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE,
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS books (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid REFERENCES app_users(id) ON DELETE SET NULL,
  title text NOT NULL,
  slug text NOT NULL UNIQUE,
  description text NOT NULL DEFAULT '',
  default_language text NOT NULL DEFAULT 'zh-CN',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id uuid NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  source_kind text NOT NULL DEFAULT 'chapter',
  source_ref text NOT NULL,
  title text NOT NULL DEFAULT '',
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(book_id, source_kind, source_ref)
);

CREATE TABLE IF NOT EXISTS document_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  book_id uuid NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chunk_index integer NOT NULL,
  content text NOT NULL,
  token_count integer NOT NULL DEFAULT 0,
  embedding vector(768),
  search_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_book ON document_chunks(book_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_search ON document_chunks USING gin(search_tsv);
CREATE INDEX IF NOT EXISTS idx_document_chunks_trgm ON document_chunks USING gin(content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
  ON document_chunks USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE TABLE IF NOT EXISTS chapter_facts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id uuid NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  document_id uuid REFERENCES documents(id) ON DELETE CASCADE,
  chapter integer NOT NULL,
  title text NOT NULL DEFAULT '',
  viewpoint text NOT NULL DEFAULT '',
  summary text NOT NULL DEFAULT '',
  story_phase text NOT NULL DEFAULT '',
  story_time_label text NOT NULL DEFAULT '',
  scene_count integer NOT NULL DEFAULT 0,
  token_estimate integer NOT NULL DEFAULT 0,
  characters jsonb NOT NULL DEFAULT '[]'::jsonb,
  locations jsonb NOT NULL DEFAULT '[]'::jsonb,
  factions jsonb NOT NULL DEFAULT '[]'::jsonb,
  concepts jsonb NOT NULL DEFAULT '[]'::jsonb,
  items jsonb NOT NULL DEFAULT '[]'::jsonb,
  relationships jsonb NOT NULL DEFAULT '[]'::jsonb,
  events jsonb NOT NULL DEFAULT '[]'::jsonb,
  confidence numeric(4,3) NOT NULL DEFAULT 0.500,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(book_id, chapter)
);

CREATE INDEX IF NOT EXISTS idx_chapter_facts_book_chapter ON chapter_facts(book_id, chapter);
CREATE INDEX IF NOT EXISTS idx_chapter_facts_story_time ON chapter_facts(book_id, story_phase, story_time_label);
CREATE INDEX IF NOT EXISTS idx_chapter_facts_events ON chapter_facts USING gin(events);

CREATE TABLE IF NOT EXISTS character_cards (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id uuid NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  name text NOT NULL,
  aliases text[] NOT NULL DEFAULT '{}',
  identity text NOT NULL DEFAULT '',
  appearance text NOT NULL DEFAULT '',
  personality text NOT NULL DEFAULT '',
  speech_style text NOT NULL DEFAULT '',
  current_status text NOT NULL DEFAULT '',
  secrets text NOT NULL DEFAULT '',
  sample_dialogue text[] NOT NULL DEFAULT '{}',
  token_budget integer NOT NULL DEFAULT 450,
  priority integer NOT NULL DEFAULT 100,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(book_id, name)
);

CREATE INDEX IF NOT EXISTS idx_character_cards_book ON character_cards(book_id);
CREATE INDEX IF NOT EXISTS idx_character_cards_aliases ON character_cards USING gin(aliases);

CREATE TABLE IF NOT EXISTS worldbook_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id uuid NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  title text NOT NULL,
  content text NOT NULL,
  keys text[] NOT NULL DEFAULT '{}',
  regex_keys text[] NOT NULL DEFAULT '{}',
  priority integer NOT NULL DEFAULT 50,
  token_budget integer NOT NULL DEFAULT 600,
  insertion_position text NOT NULL DEFAULT 'worldbook',
  sticky_turns integer NOT NULL DEFAULT 0,
  cooldown_turns integer NOT NULL DEFAULT 0,
  probability numeric(5,2) NOT NULL DEFAULT 100.00,
  character_filter text[] NOT NULL DEFAULT '{}',
  scene_filter text[] NOT NULL DEFAULT '{}',
  enabled boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_worldbook_book ON worldbook_entries(book_id);
CREATE INDEX IF NOT EXISTS idx_worldbook_keys ON worldbook_entries USING gin(keys);
CREATE INDEX IF NOT EXISTS idx_worldbook_enabled ON worldbook_entries(book_id, enabled, priority DESC);

CREATE TABLE IF NOT EXISTS game_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id uuid NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  title text NOT NULL DEFAULT '未命名会话',
  model_name text NOT NULL DEFAULT '',
  state jsonb NOT NULL DEFAULT '{}'::jsonb,
  memory_mode text NOT NULL DEFAULT 'normal',
  permission_mode text NOT NULL DEFAULT 'full_access',
  worldline jsonb NOT NULL DEFAULT '{}'::jsonb,
  turn integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_game_sessions_user_book ON game_sessions(user_id, book_id);

CREATE TABLE IF NOT EXISTS worldline_variables (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
  key text NOT NULL,
  value text NOT NULL,
  locked boolean NOT NULL DEFAULT true,
  source text NOT NULL DEFAULT 'user',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(session_id, key)
);

CREATE TABLE IF NOT EXISTS worldline_projections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
  turn integer NOT NULL,
  projection text NOT NULL,
  validated boolean NOT NULL DEFAULT false,
  validation_status text NOT NULL DEFAULT 'none',
  variables_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_worldline_variables_session ON worldline_variables(session_id);
CREATE INDEX IF NOT EXISTS idx_worldline_projections_session_turn ON worldline_projections(session_id, turn DESC);

CREATE TABLE IF NOT EXISTS messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
  turn integer NOT NULL,
  role text NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_session_turn ON messages(session_id, turn, created_at);

CREATE TABLE IF NOT EXISTS memories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid REFERENCES game_sessions(id) ON DELETE CASCADE,
  book_id uuid REFERENCES books(id) ON DELETE CASCADE,
  user_id uuid REFERENCES app_users(id) ON DELETE CASCADE,
  bucket text NOT NULL CHECK (bucket IN ('pinned', 'facts', 'abilities', 'resources', 'notes', 'summary')),
  content text NOT NULL,
  importance integer NOT NULL DEFAULT 50,
  source_message_id uuid REFERENCES messages(id) ON DELETE SET NULL,
  embedding vector(768),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_session_bucket ON memories(session_id, bucket, importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw
  ON memories USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE TABLE IF NOT EXISTS context_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
  turn integer NOT NULL,
  user_input text NOT NULL,
  layers jsonb NOT NULL DEFAULT '[]'::jsonb,
  active_character_cards jsonb NOT NULL DEFAULT '[]'::jsonb,
  active_worldbook jsonb NOT NULL DEFAULT '[]'::jsonb,
  retrieved_chunks jsonb NOT NULL DEFAULT '[]'::jsonb,
  estimated_tokens integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_context_runs_session_turn ON context_runs(session_id, turn DESC);

CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['app_users', 'books', 'chapter_facts', 'character_cards', 'worldbook_entries', 'game_sessions', 'worldline_variables', 'memories']
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_%I_touch_updated_at ON %I', t, t);
    EXECUTE format('CREATE TRIGGER trg_%I_touch_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION touch_updated_at()', t, t);
  END LOOP;
END;
$$;
