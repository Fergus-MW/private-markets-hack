import os
import httpx

# All document text and names are bound parameters, never interpolated SQL.
SAVE = """
BEGIN TRANSACTION;
UPSERT type::thing('document', $document.key) CONTENT $document;
DELETE mention WHERE document = type::thing('document', $document.key);
FOR $entity IN $entities {
    LET $rid = type::thing($entity.kind, $entity.key);
    LET $old = SELECT * FROM ONLY $rid;
    UPSERT $rid MERGE $entity;
    UPDATE $rid SET aliases = array::union($old.aliases ?? [], $entity.aliases);
};
FOR $mention IN $mentions {
    UPSERT type::thing('mention', $mention.key) CONTENT $mention;
    UPDATE type::thing('mention', $mention.key) SET
        document = type::thing('document', $document.key),
        entity = type::thing($mention.kind, $mention.entity_key);
};
COMMIT TRANSACTION;
"""


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

    def save(self, document, entities, mentions):
        self.query(SAVE, {"document": document, "entities": entities, "mentions": mentions})
