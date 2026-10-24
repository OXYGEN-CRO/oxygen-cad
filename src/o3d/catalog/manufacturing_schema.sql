-- Additive migration 2. Original provider storage policies and migration 1 remain intact.
CREATE TABLE manufacturing_providers (
    key text PRIMARY KEY, name text NOT NULL, aliases text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE manufacturing_bundles (
    id uuid PRIMARY KEY, provider_key text NOT NULL REFERENCES manufacturing_providers,
    task text NOT NULL, payload_sha256 text NOT NULL, summary jsonb NOT NULL,
    blockers jsonb NOT NULL, ingested_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(provider_key,task,payload_sha256)
);
CREATE TABLE manufacturing_sources (
    id uuid PRIMARY KEY, bundle_id uuid NOT NULL REFERENCES manufacturing_bundles ON DELETE CASCADE,
    url text NOT NULL, capture_path text, http_status integer,
    captured_at timestamptz, declared_sha256 text, sha256 text,
    object_key text, verification text NOT NULL,
    metadata jsonb NOT NULL, ingested_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX manufacturing_sources_url ON manufacturing_sources(url);
CREATE TABLE manufacturing_records (
    id uuid PRIMARY KEY, provider_key text NOT NULL REFERENCES manufacturing_providers,
    external_id text NOT NULL, record_type text NOT NULL, name text NOT NULL,
    process text, material text, api_access text NOT NULL,
    source_id uuid NOT NULL REFERENCES manufacturing_sources,
    payload jsonb NOT NULL, search_text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple',search_text)) STORED,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(provider_key,external_id,record_type)
);
CREATE INDEX manufacturing_records_search ON manufacturing_records USING gin(search_vector);
CREATE INDEX manufacturing_records_facets ON manufacturing_records(provider_key,record_type,process,material);
CREATE INDEX manufacturing_records_facts ON manufacturing_records USING gin(payload jsonb_path_ops);
CREATE TABLE manufacturing_constraints (
    record_id uuid NOT NULL REFERENCES manufacturing_records ON DELETE CASCADE,
    ordinal integer NOT NULL, key text NOT NULL, value jsonb NOT NULL,
    numeric_value double precision, unit text, source_field text,
    PRIMARY KEY(record_id,ordinal)
);
CREATE INDEX manufacturing_constraints_numeric ON manufacturing_constraints(key,unit,numeric_value)
    WHERE numeric_value IS NOT NULL AND source_field IS NOT NULL;
-- Quarantine deliberately excludes restricted source payloads and vendor offers.
CREATE TABLE manufacturing_quarantine (
    bundle_id uuid NOT NULL REFERENCES manufacturing_bundles ON DELETE CASCADE,
    ordinal integer NOT NULL, external_id text, record_type text, reason text NOT NULL,
    PRIMARY KEY(bundle_id,ordinal)
);
