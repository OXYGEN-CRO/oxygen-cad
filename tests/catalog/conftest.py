import os

import pytest

pytest.importorskip("psycopg", reason="Optional catalog dependencies are not installed")

from o3d.catalog.db import Catalog
from o3d.catalog.models import Provider
from o3d.catalog.objects import Objects


@pytest.fixture
def catalog(tmp_path):
    dsn = os.environ.get("O3D_CATALOG_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set O3D_CATALOG_TEST_DATABASE_URL to a disposable PostgreSQL/pgvector database")
    with Catalog(dsn, Objects(tmp_path / "objects")) as db:
        name = db.conn.execute("SELECT current_database() AS name").fetchone()["name"]
        if not name.endswith("_test"):
            pytest.fail("Refusing to clear a database without an _test suffix")
        db.migrate()
        db.conn.execute("TRUNCATE providers CASCADE")
        db.register_provider(Provider(id="test", name="Fixture", website="https://example.com", access="stored",
            license="MIT", terms_url="https://example.com/license"))
        yield db
