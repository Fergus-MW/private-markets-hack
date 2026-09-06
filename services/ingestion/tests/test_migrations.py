import os
import unittest
import uuid

from app.migrations import GRAPH, PROJECT, apply, steps


class FakeStore:
    """Records statements and replays whatever the migrations claim to have applied."""

    def __init__(self, applied=()):
        self.applied = list(applied)
        self.statements = []

    def query(self, sql, variables=None):
        self.statements.append((sql, variables))
        if sql.startswith("SELECT name FROM schema_migration"):
            return [{"result": [{"name": name} for name in self.applied]}]
        self.applied.append(variables["name"])
        return [{"result": None}]


class SelectionTests(unittest.TestCase):
    def test_prefix_picks_the_right_migration_set(self):
        self.assertIs(steps("user_" + "a" * 64), GRAPH)
        self.assertIs(steps("project_" + "a" * 64), PROJECT)

    def test_names_are_unique_and_ordered(self):
        for series in (GRAPH, PROJECT):
            names = [name for name, _ in series]
            self.assertEqual(names, sorted(names), "migrations must apply in name order")
            self.assertEqual(len(names), len(set(names)), "duplicate migration name")

    def test_every_definition_is_idempotent(self):
        """A retried migration must be harmless: no bare DEFINE, no OVERWRITE."""
        for series in (GRAPH, PROJECT):
            for name, statements in series:
                for statement in (s.strip() for s in statements.split(";") if s.strip()):
                    self.assertTrue(statement.startswith("DEFINE "), f"{name}: {statement}")
                    self.assertIn("IF NOT EXISTS", statement, f"{name} is not idempotent: {statement}")


class ApplyTests(unittest.TestCase):
    def test_fresh_database_applies_everything_in_order(self):
        store = FakeStore()
        self.assertEqual(apply("project_" + "a" * 64, store), [name for name, _ in PROJECT])

    def test_applying_twice_is_a_no_op(self):
        database = "project_" + "a" * 64
        store = FakeStore()
        apply(database, store)
        before = len(store.statements)
        self.assertEqual(apply(database, store), [])
        self.assertEqual(len(store.statements), before + 1, "only the read should repeat")

    def test_a_database_created_before_a_migration_still_receives_it(self):
        """The whole point: IF NOT EXISTS alone never reaches existing databases."""
        store = FakeStore(applied=[PROJECT[0][0]])
        self.assertEqual(apply("project_" + "a" * 64, store), [name for name, _ in PROJECT[1:]])

    def test_each_migration_commits_with_its_own_record(self):
        store = FakeStore()
        apply("user_" + "a" * 64, store)
        written = [sql for sql, _ in store.statements if sql.startswith("BEGIN")]
        self.assertEqual(len(written), len(GRAPH))
        for sql in written:
            self.assertIn("schema_migration", sql)
            self.assertTrue(sql.endswith("COMMIT TRANSACTION;"))


@unittest.skipUnless(os.environ.get("KG_DB_TESTS") == "1", "Set KG_DB_TESTS=1 for isolated SurrealDB integration")
class LiveMigrationTests(unittest.TestCase):
    """Runs against a real SurrealDB: make up, then make test-live."""

    def setUp(self):
        from app.store import Store
        self.database = "project_" + uuid.uuid4().hex + "a" * 32
        self.database = self.database[:len("project_") + 64]
        admin = Store(namespace="projects", database="catalog",
                      user=os.environ.get("SURREAL_USER", "root"),
                      password=os.environ["SURREAL_PASSWORD"],
                      auth_level=os.environ.get("SURREAL_AUTH_LEVEL", "root"))
        admin.query(f"DEFINE NAMESPACE IF NOT EXISTS projects; USE NS projects; DEFINE DATABASE IF NOT EXISTS {self.database};")
        self.store = Store(namespace="projects", database=self.database,
                           user=os.environ.get("SURREAL_USER", "root"),
                           password=os.environ["SURREAL_PASSWORD"],
                           auth_level=os.environ.get("SURREAL_AUTH_LEVEL", "root"))
        self.addCleanup(lambda: admin.query(f"USE NS projects; REMOVE DATABASE IF EXISTS {self.database};"))

    def tables(self):
        info = self.store.query("INFO FOR DB;")[0]["result"]
        return set(info.get("tables", {}))

    def test_tables_and_indexes_are_created_and_recorded_once(self):
        self.assertEqual(apply(self.database, self.store), [name for name, _ in PROJECT])
        self.assertTrue({"node", "run", "artifact", "schema_migration"} <= self.tables())
        indexes = self.store.query("INFO FOR TABLE node;")[0]["result"].get("indexes", {})
        self.assertIn("node_kind", indexes)
        recorded = self.store.query("SELECT name FROM schema_migration;")[0]["result"]
        self.assertEqual(sorted(r["name"] for r in recorded), sorted(name for name, _ in PROJECT))
        # Re-running must neither duplicate records nor fail.
        self.assertEqual(apply(self.database, self.store), [])
        again = self.store.query("SELECT name FROM schema_migration;")[0]["result"]
        self.assertEqual(len(again), len(PROJECT))

    def test_a_missing_later_migration_is_applied_to_an_existing_database(self):
        """Simulates the real gap: database provisioned before 0002 existed."""
        name, statements = PROJECT[0]
        self.store.query("BEGIN TRANSACTION;" + statements +
                         "UPSERT type::thing('schema_migration', $name) CONTENT {name: $name};COMMIT TRANSACTION;",
                         {"name": name})
        self.assertNotIn("run_job_id", self.store.query("INFO FOR TABLE run;")[0]["result"].get("indexes", {}))
        self.assertEqual(apply(self.database, self.store), [PROJECT[1][0]])
        self.assertIn("run_job_id", self.store.query("INFO FOR TABLE run;")[0]["result"].get("indexes", {}))


if __name__ == "__main__":
    unittest.main()
