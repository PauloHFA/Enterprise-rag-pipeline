"""Bounded-label metrics; tenant identities and document IDs never become labels."""

from fastapi import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

DOCUMENTS_UPLOADED = Counter(
    "enterprise_rag_documents_uploaded_total", "Accepted new documents", ["status"]
)
DOCUMENTS_DELETED = Counter("enterprise_rag_documents_deleted_total", "Documents deleted")
DOCUMENTS_REINDEXED = Counter(
    "enterprise_rag_documents_reindexed_total", "Reindexes accepted", ["status"]
)
SEARCH_REQUESTS = Counter(
    "enterprise_rag_search_requests_total", "Search requests", ["mode", "status"]
)
SEARCH_LATENCY = Histogram("enterprise_rag_search_latency_seconds", "Search latency", ["mode"])
SEARCH_RESULTS_COUNT = Histogram("enterprise_rag_search_results_count", "Search results", ["mode"])
API_REQUESTS_TOTAL = Counter(
    "enterprise_rag_api_requests_total", "HTTP requests", ["method", "endpoint", "status"]
)
API_REQUEST_LATENCY = Histogram(
    "enterprise_rag_api_request_latency_seconds", "HTTP latency", ["method", "endpoint"]
)
QUEUE_MESSAGES_PUBLISHED = Counter(
    "enterprise_rag_queue_messages_published_total", "Confirmed outbox deliveries", ["queue_name"]
)
OUTBOX_PENDING = Gauge("enterprise_rag_outbox_pending", "Unpublished events")
OUTBOX_OLDEST = Gauge("enterprise_rag_outbox_oldest_seconds", "Age of oldest pending event")


def get_metrics_response():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_document_upload(tenant_id, status):
    DOCUMENTS_UPLOADED.labels(status).inc()


def record_document_deleted(tenant_id):
    DOCUMENTS_DELETED.inc()


def record_document_reindexed(tenant_id, status):
    DOCUMENTS_REINDEXED.labels(status).inc()


def record_search_request(mode, status, latency, results_count):
    SEARCH_REQUESTS.labels(mode, status).inc()
    SEARCH_LATENCY.labels(mode).observe(latency)
    SEARCH_RESULTS_COUNT.labels(mode).observe(results_count)


def record_queue_publish(queue_name):
    QUEUE_MESSAGES_PUBLISHED.labels(queue_name).inc()


def record_api_request(method, endpoint, status, latency):
    API_REQUESTS_TOTAL.labels(method, endpoint, str(status)).inc()
    API_REQUEST_LATENCY.labels(method, endpoint).observe(latency)
