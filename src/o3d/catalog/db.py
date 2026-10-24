from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import BOM, Part, Provider, Search
from .objects import Objects

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


def stable_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "oxygen-parts:" + value))


def text_for(part: Part) -> str:
    return " ".join(str(x) for x in [part.name, part.description, part.external_id,
        part.manufacturer or "", part.mpn or "", part.supplier_sku or "", part.category,
        part.family or "", *part.aliases, *part.tags, json.dumps(part.attributes, sort_keys=True),
        *[f"{s.key} {s.value} {s.unit or ''}" for s in part.specs]])


class Catalog:
    def __init__(self, dsn: str | None = None, objects: Objects | None = None):
        dsn = dsn or os.environ.get("O3D_CATALOG_DATABASE_URL")
        if not dsn:
            raise ValueError("Set O3D_CATALOG_DATABASE_URL; see docs/CATALOG.md")
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self.conn.execute("SET search_path TO o3d_catalog, public")
        self.conn.execute("SET statement_timeout TO '30s'")
        self.objects = objects or Objects()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def migrate(self):
        schema = Path(__file__).with_name("schema.sql").read_text()
        digest = hashlib.sha256(schema.encode()).hexdigest()
        with self.conn.transaction():
            self.conn.execute("SELECT pg_advisory_xact_lock(634917023)")
            exists = self.conn.execute("SELECT to_regclass('o3d_catalog.schema_versions') AS t").fetchone()["t"]
            if exists:
                row = self.conn.execute("SELECT sha256 FROM schema_versions WHERE version=1").fetchone()
                if row:
                    if row["sha256"] != digest:
                        raise ValueError("Schema 1 checksum changed; add a migration rather than editing a deployed migration")
                    return {"version": 1, "changed": False}
            self.conn.execute(schema)
            self.conn.execute("INSERT INTO schema_versions(version,sha256) VALUES (1,%s)", (digest,))
        return {"version": 1, "changed": True}

    def register_provider(self, provider: Provider):
        self.conn.execute("""INSERT INTO providers(id,name,website,access,license,terms_url,notes)
            VALUES (%(id)s,%(name)s,%(website)s,%(access)s,%(license)s,%(terms_url)s,%(notes)s)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, website=excluded.website,
            access=excluded.access,license=excluded.license,terms_url=excluded.terms_url,
            notes=excluded.notes,updated_at=now()""", provider.model_dump())

    def require_stored(self, provider_id: str):
        row = self.conn.execute("SELECT * FROM providers WHERE id=%s", (provider_id,)).fetchone()
        if not row or row["access"] != "stored":
            raise ValueError("Provider is not approved for catalog storage")
        return row

    def document(self, provider_id: str, url: str, data: bytes, media_type="application/json"):
        self.require_stored(provider_id)
        digest, key = self.objects.put(data)
        doc_id = stable_id(f"document:{provider_id}:{url}:{digest}")
        self.conn.execute("""INSERT INTO documents(id,provider_id,url,sha256,object_key,media_type)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (doc_id, provider_id, url, digest, key, media_type))
        return doc_id

    def upsert(self, provider_id: str, part: Part, document_id: str):
        self.require_stored(provider_id)
        doc = self.conn.execute("SELECT provider_id FROM documents WHERE id=%s", (document_id,)).fetchone()
        if not doc or doc["provider_id"] != provider_id:
            raise ValueError("Source document must belong to this provider")
        part_id = stable_id(f"part:{provider_id}:{part.external_id}")
        search_text = text_for(part)
        values = part.model_dump(exclude={"specs", "ports", "assets"})
        values.update(id=part_id, provider_id=provider_id, document_id=document_id, search_text=search_text)
        values["attributes"], values["identifiers"] = Jsonb(part.attributes), Jsonb(part.identifiers)
        with self.conn.transaction():
            self.conn.execute("SELECT id FROM parts WHERE id=%s FOR UPDATE", (part_id,))
            self.conn.execute("""DELETE FROM embeddings WHERE part_id=%s AND content_sha256<>%s""",
                (part_id, hashlib.sha256(search_text.encode()).hexdigest()))
            self.conn.execute("""INSERT INTO parts(id,provider_id,external_id,name,description,category,family,
                manufacturer,mpn,supplier_sku,product_url,lifecycle,procurement,tags,aliases,identifiers,
                attributes,document_id,observed_at,search_text) VALUES
                (%(id)s,%(provider_id)s,%(external_id)s,%(name)s,%(description)s,%(category)s,%(family)s,
                %(manufacturer)s,%(mpn)s,%(supplier_sku)s,%(product_url)s,%(lifecycle)s,%(procurement)s,
                %(tags)s,%(aliases)s,%(identifiers)s,%(attributes)s,%(document_id)s,%(observed_at)s,%(search_text)s)
                ON CONFLICT(provider_id,external_id) DO UPDATE SET name=excluded.name,
                description=excluded.description,category=excluded.category,family=excluded.family,
                manufacturer=excluded.manufacturer,mpn=excluded.mpn,supplier_sku=excluded.supplier_sku,
                product_url=excluded.product_url,lifecycle=excluded.lifecycle,procurement=excluded.procurement,
                tags=excluded.tags,aliases=excluded.aliases,identifiers=excluded.identifiers,
                attributes=excluded.attributes,document_id=excluded.document_id,
                observed_at=excluded.observed_at,search_text=excluded.search_text,updated_at=now()""", values)
            self.conn.execute("DELETE FROM specs WHERE part_id=%s", (part_id,))
            for spec in part.specs:
                number = float(spec.value) if type(spec.value) in (float, int) else None
                self.conn.execute("INSERT INTO specs VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (part_id, spec.key, Jsonb(spec.value), number, spec.unit, spec.source_field, spec.evidence, document_id))
            self.conn.execute("DELETE FROM ports WHERE part_id=%s", (part_id,))
            for port in part.ports:
                self.conn.execute("INSERT INTO ports VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (part_id, port.name, port.kind, port.interface, Jsonb(port.properties), Jsonb(port.frame),
                     port.evidence, port.source_field, document_id))
            # Removed files remain auditable but are no longer eligible for retrieval.
            self.conn.execute("UPDATE assets SET active=false WHERE part_id=%s", (part_id,))
            for asset in part.assets:
                av = asset.model_dump()
                av.update(id=stable_id(f"asset:{part_id}:{asset.url}"), part_id=part_id, document_id=document_id)
                self.conn.execute("""INSERT INTO assets(id,part_id,url,format,kind,sha256,byte_size,license,
                    license_url,attribution,download_allowed,scope,document_id) VALUES
                    (%(id)s,%(part_id)s,%(url)s,%(format)s,%(kind)s,%(sha256)s,%(byte_size)s,%(license)s,
                    %(license_url)s,%(attribution)s,%(download_allowed)s,%(scope)s,%(document_id)s)
                    ON CONFLICT(part_id,url) DO UPDATE SET format=excluded.format,kind=excluded.kind,
                    object_key=CASE WHEN assets.sha256 IS NOT DISTINCT FROM excluded.sha256 THEN assets.object_key END,
                    downloaded_at=CASE WHEN assets.sha256 IS NOT DISTINCT FROM excluded.sha256 THEN assets.downloaded_at END,
                    sha256=excluded.sha256,byte_size=excluded.byte_size,license=excluded.license,
                    license_url=excluded.license_url,attribution=excluded.attribution,
                    download_allowed=excluded.download_allowed,scope=excluded.scope,document_id=excluded.document_id,
                    active=true""", av)
        return part_id

    def get(self, part_id: str):
        row = self.conn.execute("SELECT * FROM parts WHERE id=%s", (part_id,)).fetchone()
        if not row:
            raise KeyError(part_id)
        row.pop("search_vector")
        row.pop("search_text")
        row["specs"] = self.conn.execute("SELECT * FROM specs WHERE part_id=%s ORDER BY key", (part_id,)).fetchall()
        row["ports"] = self.conn.execute("SELECT * FROM ports WHERE part_id=%s ORDER BY name", (part_id,)).fetchall()
        row["assets"] = self.conn.execute("SELECT * FROM assets WHERE part_id=%s AND active ORDER BY kind,format,url", (part_id,)).fetchall()
        row["provenance"] = self.conn.execute("SELECT * FROM documents WHERE id=%s", (row["document_id"],)).fetchone()
        row["supplier"] = self.conn.execute("SELECT * FROM providers WHERE id=%s", (row["provider_id"],)).fetchone()
        row["availability"] = {"state": "unknown", "reason": "No live offer checked; catalog presence does not establish stock"}
        row["compatibility"] = {"state": "unverified", "reason": "Requires mating geometry, electrical limits and assembly verification"}
        return row

    def search(self, request: Search, query_vector: list[float] | None = None):
        clauses, args = ["true"], {"q": request.q, "limit": request.limit, "offset": request.offset,
            "pattern": "%" + request.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"}
        for key in ("category", "family", "provider", "manufacturer", "procurement"):
            if value := getattr(request, key):
                column = "provider_id" if key == "provider" else key
                clauses.append(f"p.{column}=%({key})s")
                args[key] = value
        if request.tags:
            clauses.append("p.tags @> %(tags)s::text[]")
            args["tags"] = request.tags
        if request.attributes:
            clauses.append("p.attributes @> %(attributes)s::jsonb")
            args["attributes"] = Jsonb(request.attributes)
        for i, numeric in enumerate(request.numeric):
            conditions = [f"s.key=%(key{i})s", f"s.unit=%(unit{i})s"]
            args[f"key{i}"], args[f"unit{i}"] = numeric.key, numeric.unit
            for bound, operator in (("min", ">="), ("max", "<=")):
                if (value := getattr(numeric, bound)) is not None:
                    conditions.append(f"s.numeric_value{operator}%({bound}{i})s")
                    args[f"{bound}{i}"] = value
            clauses.append("EXISTS (SELECT 1 FROM specs s WHERE s.part_id=p.id AND " + " AND ".join(conditions) + ")")
        if request.cad_required or request.downloadable_cad:
            allowed = " AND a.download_allowed" if request.downloadable_cad else ""
            clauses.append("EXISTS (SELECT 1 FROM assets a WHERE a.part_id=p.id AND a.active AND a.kind='cad'" + allowed + ")")
        where = " AND ".join(clauses)
        # Numeric and categorical constraints apply before BOTH retrieval channels.
        lexical_match = """(%(q)s='' OR p.search_vector @@ websearch_to_tsquery('simple',%(q)s)
            OR p.name ILIKE %(pattern)s OR p.external_id=%(q)s OR p.mpn=%(q)s
            OR p.supplier_sku=%(q)s OR %(q)s=ANY(p.aliases) OR similarity(p.name,%(q)s)>=0.2)"""
        lexical_score = """(CASE WHEN lower(p.external_id)=lower(%(q)s) OR lower(p.mpn)=lower(%(q)s)
            OR lower(p.supplier_sku)=lower(%(q)s) OR %(q)s=ANY(p.aliases) THEN 10 ELSE 0 END
            + ts_rank_cd(p.search_vector,websearch_to_tsquery('simple',%(q)s))
            + similarity(p.name,%(q)s))"""
        args["model"] = EMBEDDING_MODEL
        hybrid = request.mode == "hybrid" and bool(request.q)
        if hybrid and query_vector is None:
            from .semantic import embed_query
            query_vector = embed_query(request.q)
        if hybrid:
            if len(query_vector) != 384:
                raise ValueError("Expected a 384-dimensional embedding")
            args["vector"] = "[" + ",".join(str(float(v)) for v in query_vector) + "]"
            ranking = f""", lexical AS (
                SELECT p.id, row_number() OVER (ORDER BY {lexical_score} DESC,p.id) AS rank
                FROM filtered p WHERE {lexical_match}), semantic AS (
                SELECT p.id,row_number() OVER (ORDER BY e.embedding <=> %(vector)s::vector,p.id) AS rank
                FROM filtered p JOIN embeddings e ON e.part_id=p.id AND e.model=%(model)s),
                ranked AS (SELECT coalesce(l.id,s.id) AS id,
                    coalesce(1.0/(60+l.rank),0)+coalesce(1.0/(60+s.rank),0) AS score,
                    l.rank AS lexical_rank,s.rank AS semantic_rank
                    FROM lexical l FULL JOIN semantic s ON s.id=l.id)"""
        else:
            ranking = f""", ranked AS (SELECT p.id,{lexical_score} AS score,
                row_number() OVER (ORDER BY {lexical_score} DESC,p.id) AS lexical_rank,
                NULL::bigint AS semantic_rank FROM filtered p WHERE {lexical_match})"""
        rows = self.conn.execute(f"""WITH filtered AS MATERIALIZED (SELECT p.* FROM parts p WHERE {where})
            {ranking}
            SELECT p.id,p.provider_id,p.external_id,p.name,p.category,p.family,p.manufacturer,p.mpn,
                p.supplier_sku,p.product_url,p.procurement,p.lifecycle,p.tags,p.attributes,p.observed_at,
                r.score,r.lexical_rank,r.semantic_rank,
                (SELECT count(*) FROM assets a WHERE a.part_id=p.id AND a.active AND a.kind='cad') AS cad_count,
                (SELECT count(*) FROM assets a WHERE a.part_id=p.id AND a.active AND a.kind='cad' AND a.download_allowed) AS downloadable_cad_count,
                count(*) OVER() AS total
            FROM ranked r JOIN filtered p ON p.id=r.id
            ORDER BY r.score DESC,p.name,p.id LIMIT %(limit)s OFFSET %(offset)s""", args).fetchall()
        total = rows[0]["total"] if rows else 0
        if not rows and request.offset:
            total = self.conn.execute(f"WITH filtered AS MATERIALIZED (SELECT p.* FROM parts p WHERE {where})"
                + ranking + " SELECT count(*) AS n FROM ranked", args).fetchone()["n"]
        for row in rows:
            row.pop("total")
            row["availability"] = "unknown"
            row["compatibility"] = "unverified"
        coverage = self.conn.execute("SELECT count(*) AS n FROM embeddings WHERE model=%s", (EMBEDDING_MODEL,)).fetchone()["n"]
        return {"items": rows, "total": total, "limit": request.limit, "offset": request.offset,
            "retrieval": "hybrid-rrf" if hybrid else "exact-fulltext-trigram",
            "embedded_parts": coverage, "warnings": ["Stock and physical compatibility require verification"]
            + (["Semantic index is empty; run embed"] if hybrid and not coverage else [])}

    def status(self):
        result = {}
        for table in ("parts", "specs", "ports", "assets", "documents", "embeddings"):
            result[table] = self.conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        result["providers"] = self.conn.execute("""SELECT v.*,count(p.id) AS parts FROM providers v
            LEFT JOIN parts p ON p.provider_id=v.id GROUP BY v.id ORDER BY v.id""").fetchall()
        result["runs"] = self.conn.execute("SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT 10").fetchall()
        return result

    def plan(self, bom: BOM):
        items = []
        for item in bom.items:
            value = item.model_dump()
            gaps = []
            if item.kind == "custom":
                gaps.append("design_and_manufacture_required")
            elif not item.part_id:
                gaps.append("select_catalog_part")
            else:
                part = self.get(item.part_id)
                value["part"] = part
                if part["procurement"] != "off_the_shelf":
                    gaps.append("off_the_shelf_status_unverified")
                if part["lifecycle"] in ("obsolete", "discontinued"):
                    gaps.append("part_discontinued")
                if not any(a["kind"] == "cad" and a["format"] in ("step", "stp") and a["download_allowed"] for a in part["assets"]):
                    gaps.append("importable_cad_missing_or_permission_unknown")
                if not part["ports"]:
                    gaps.append("mounting_and_interfaces_missing")
                specs = {s["key"]: s for s in part["specs"]}
                for req in item.requirements:
                    spec = specs.get(req.key)
                    if not spec or spec["unit"] != req.unit or spec["numeric_value"] is None:
                        gaps.append(f"requirement_unknown:{req.key}:{req.unit}")
                    elif ((req.min is not None and spec["numeric_value"] < req.min)
                          or (req.max is not None and spec["numeric_value"] > req.max)):
                        gaps.append(f"requirement_failed:{req.key}")
                gaps.extend(["live_stock_price_and_shipping_unchecked", "assembly_fit_unverified"])
            value["gaps"] = gaps
            items.append(value)
        return {"name": bom.name, "items": items, "ready_to_build": False,
            "note": "Catalog selection is not a physical fit, electrical compatibility or procurement approval"}
