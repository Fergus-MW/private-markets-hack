import os
import httpx

# One document record contains both source elements and agent context. An upsert
# atomically replaces the complete output; no entity or mention writes occur.
SAVE = "UPSERT type::thing('document', $document.key) CONTENT $document;"


class Store:
    def query(self, sql, variables=None):
        with httpx.Client(timeout=60) as client:
            url = os.environ["SURREAL_URL"].rstrip("/")
            credentials = {"user": os.environ.get("SURREAL_USER", "ingestion"),
                           "pass": os.environ["SURREAL_PASSWORD"]}
            if os.environ.get("SURREAL_AUTH_LEVEL", "database") == "database":
                credentials.update({"NS": "markets", "DB": "documents"})
            signin = client.post(url + "/signin", headers={"Accept": "application/json"}, json=credentials)
            signin.raise_for_status()
            token = signin.json().get("token")
            if not token:
                raise RuntimeError("SurrealDB authentication failed")
            response = client.post(
                url + "/rpc",
                headers={"Accept": "application/json", "Surreal-NS": "markets", "Surreal-DB": "documents",
                         "Authorization": "Bearer " + token},
                json={"id": 1, "method": "query", "params": [sql, variables or {}]},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("error") or not isinstance(payload.get("result"), list):
            raise RuntimeError("SurrealDB RPC failed")
        results = payload["result"]
        if any(result.get("status") != "OK" for result in results):
            raise RuntimeError("SurrealDB statement failed; transaction was not acknowledged")
        return results

    def save(self, document):
        self.query(SAVE, {"document": document})

    def get(self, key):
        rows = self.query("SELECT * FROM type::thing('document', $key);", {"key": key})[0]["result"]
        return rows[0] if rows else None


class GraphStore(Store):
    """Single-workspace graph with optimistic concurrency and real SurrealDB edges.

    The snapshot is the validated write model; kg_node/kg_link are its atomic
    graph projection. A stale revision never overwrites another ingestion run.
    """
    def load_graph(self):
        from app.graph import Graph, GraphState
        rows = self.query("SELECT * FROM type::thing('kg_state', 'workspace');")[0]["result"]
        if not rows:
            return Graph()
        row = rows[0]
        row.pop("id", None)
        # Legacy snapshots remain readable; the next atomic save removes this field.
        row.pop("facts", None)
        return Graph(GraphState.model_validate(row))

    def save_graph(self, graph):
        state = graph.state.model_dump(mode="json")
        expected = state["revision"]
        state["revision"] += 1
        nodes = list(state["entities"].values()) + list(state["sources"].values())
        edges = []
        for edge in state["edges"].values():
            edge = dict(edge)
            for field in ("subject", "object"):
                if edge[field] in graph.state.entities:
                    edge[field] = graph.resolve(edge[field])
            edges.append(edge)
        self.query("""
BEGIN TRANSACTION;
LET $current = SELECT * FROM ONLY type::thing('kg_state', 'workspace');
IF ($current != NONE AND $current.revision != $expected) OR ($current = NONE AND $expected != 0) {
    THROW 'Graph changed concurrently; retry with a fresh snapshot';
};
UPSERT type::thing('kg_state', 'workspace') CONTENT $state;
FOR $node IN $nodes {
    UPSERT type::thing('kg_node', $node.key) CONTENT $node;
};
FOR $edge IN $edges {
    LET $from = type::thing('kg_node', $edge.subject);
    LET $to = type::thing('kg_node', $edge.object);
    LET $relation = type::thing('kg_link', $edge.key);
    DELETE $relation;
    RELATE $from->$relation->$to CONTENT $edge;
};
COMMIT TRANSACTION;
""", {"expected": expected, "state": state, "nodes": nodes, "edges": edges})
        graph.state.revision = state["revision"]
