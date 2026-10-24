CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS o3d_catalog;
SET search_path TO o3d_catalog, public;

CREATE TABLE IF NOT EXISTS providers (
    id text PRIMARY KEY, name text NOT NULL, website text NOT NULL,
    access text NOT NULL CHECK (access IN ('stored', 'live-only', 'permission-required')),
    license text NOT NULL, terms_url text NOT NULL, notes text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id uuid PRIMARY KEY, provider_id text NOT NULL REFERENCES providers,
    started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed')),
    processed integer NOT NULL DEFAULT 0, checkpoint jsonb NOT NULL DEFAULT '{}', error text
);
CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY, provider_id text NOT NULL REFERENCES providers,
    url text NOT NULL, captured_at timestamptz NOT NULL DEFAULT now(),
    sha256 text NOT NULL, object_key text NOT NULL, media_type text NOT NULL,
    UNIQUE(provider_id, url, sha256)
);
CREATE TABLE IF NOT EXISTS parts (
    id uuid PRIMARY KEY, provider_id text NOT NULL REFERENCES providers,
    external_id text NOT NULL, name text NOT NULL, description text NOT NULL,
    category text NOT NULL, family text, manufacturer text, mpn text, supplier_sku text,
    product_url text, lifecycle text NOT NULL, procurement text NOT NULL,
    tags text[] NOT NULL, aliases text[] NOT NULL, identifiers jsonb NOT NULL,
    attributes jsonb NOT NULL, document_id uuid NOT NULL REFERENCES documents,
    observed_at timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
    search_text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
    UNIQUE(provider_id, external_id)
);
CREATE INDEX IF NOT EXISTS parts_search_gin ON parts USING gin(search_vector);
CREATE INDEX IF NOT EXISTS parts_trigram_gin ON parts USING gin(name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS parts_attributes_gin ON parts USING gin(attributes jsonb_path_ops);
CREATE INDEX IF NOT EXISTS parts_tags_gin ON parts USING gin(tags);
CREATE INDEX IF NOT EXISTS parts_facets ON parts(category, family);
CREATE INDEX IF NOT EXISTS parts_manufacturer_mpn ON parts(manufacturer, mpn);
CREATE TABLE IF NOT EXISTS specs (
    part_id uuid NOT NULL REFERENCES parts ON DELETE CASCADE, key text NOT NULL,
    value jsonb NOT NULL, numeric_value double precision, unit text,
    source_field text NOT NULL, evidence text NOT NULL,
    document_id uuid NOT NULL REFERENCES documents,
    PRIMARY KEY(part_id,key)
);
CREATE INDEX IF NOT EXISTS specs_numeric ON specs(key,unit,numeric_value) WHERE numeric_value IS NOT NULL;
CREATE TABLE IF NOT EXISTS ports (
    part_id uuid NOT NULL REFERENCES parts ON DELETE CASCADE, name text NOT NULL,
    kind text NOT NULL, interface text NOT NULL, properties jsonb NOT NULL,
    frame jsonb, evidence text NOT NULL, source_field text NOT NULL,
    document_id uuid NOT NULL REFERENCES documents, PRIMARY KEY(part_id,name)
);
CREATE TABLE IF NOT EXISTS assets (
    id uuid PRIMARY KEY, part_id uuid NOT NULL REFERENCES parts ON DELETE CASCADE,
    url text NOT NULL, format text NOT NULL, kind text NOT NULL,
    sha256 text, byte_size bigint, license text NOT NULL, license_url text,
    attribution text NOT NULL, download_allowed boolean NOT NULL, scope text NOT NULL,
    object_key text, downloaded_at timestamptz, document_id uuid NOT NULL REFERENCES documents,
    active boolean NOT NULL DEFAULT true,
    UNIQUE(part_id,url)
);
CREATE INDEX IF NOT EXISTS assets_part_kind ON assets(part_id,kind);
CREATE TABLE IF NOT EXISTS embeddings (
    part_id uuid PRIMARY KEY REFERENCES parts ON DELETE CASCADE,
    model text NOT NULL, content_sha256 text NOT NULL,
    embedding vector(384) NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS embeddings_hnsw ON embeddings USING hnsw(embedding vector_cosine_ops);
-- Prices/stock deliberately have no generic storage table: each future live adapter
-- must declare its permission and TTL before any offer may be persisted.
CREATE TABLE IF NOT EXISTS schema_versions (
    version integer PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now()
);
