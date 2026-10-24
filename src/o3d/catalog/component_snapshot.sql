-- Additive migration 3: unofficial component snapshots, isolated from live offers.
CREATE TABLE component_snapshots (
    id uuid PRIMARY KEY, source text NOT NULL, url text NOT NULL,
    sha256 text NOT NULL, byte_size bigint NOT NULL, object_pointer text NOT NULL,
    captured_at timestamptz, last_modified text, metadata jsonb NOT NULL,
    verification text NOT NULL, processed bigint NOT NULL DEFAULT 0,
    complete boolean NOT NULL DEFAULT false, imported_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source,sha256)
);
CREATE TABLE component_records (
    id uuid PRIMARY KEY, source text NOT NULL, lcsc text NOT NULL,
    supplier text NOT NULL DEFAULT 'JLCPCB', origin text NOT NULL DEFAULT 'community_unofficial',
    mpn text, manufacturer text, package text, description text, category text, subcategory text,
    datasheet_url text, library_type text, basic boolean, preferred boolean,
    assembly jsonb, attrition jsonb, attributes jsonb NOT NULL, raw_metadata jsonb NOT NULL,
    parse_warnings jsonb NOT NULL, fields_removed jsonb NOT NULL,
    source_fetched_at timestamptz, snapshot_id uuid NOT NULL REFERENCES component_snapshots,
    license_status text NOT NULL DEFAULT 'unknown', active boolean NOT NULL DEFAULT true,
    search_text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
    updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source,lcsc)
);
CREATE INDEX component_records_search ON component_records USING gin(search_vector);
CREATE INDEX component_records_attributes ON component_records USING gin(attributes jsonb_path_ops);
CREATE INDEX component_records_lcsc ON component_records(lcsc) WHERE active;
CREATE INDEX component_records_mpn ON component_records(lower(mpn)) WHERE active;
CREATE INDEX component_records_manufacturer ON component_records(manufacturer) WHERE active;
CREATE INDEX component_records_package ON component_records(package) WHERE active;
CREATE INDEX component_records_category ON component_records(category) WHERE active;
CREATE INDEX component_records_snapshot ON component_records(snapshot_id);
