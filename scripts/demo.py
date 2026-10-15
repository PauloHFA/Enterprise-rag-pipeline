"""Real upload → indexed → three search modes → citations → deletion demonstration."""

import json
import uuid

from evaluation.common import cleanup, client_from_environment, wait_indexed


def main():
    marker = "demo" + uuid.uuid4().hex
    documents = []
    with client_from_environment() as client:
        try:
            response = client.post(
                "/v1/documents",
                files={
                    "file": (
                        "demo.txt",
                        f"{marker} O prazo contratual de pagamento é trinta dias.".encode(),
                        "text/plain",
                    )
                },
            )
            response.raise_for_status()
            document_id = response.json()["id"]
            documents.append(document_id)
            indexed = wait_indexed(client, documents)
            print(
                json.dumps({"document_id": document_id, "status": indexed[document_id]["status"]})
            )
            for mode in ("lexical", "vector", "hybrid"):
                result = client.post("/v1/search", json={"query": marker, "mode": mode})
                result.raise_for_status()
                hits = [
                    hit for hit in result.json()["results"] if hit["document_id"] == document_id
                ]
                if not hits:
                    raise RuntimeError(f"{mode} did not retrieve the demo document")
                print(
                    json.dumps({"mode": mode, "citation": hits[0]["citation"]}, ensure_ascii=False)
                )
        finally:
            cleanup(client, documents)
    print("Demo document deleted; original storage cleanup is delivered by the outbox.")


if __name__ == "__main__":
    main()
