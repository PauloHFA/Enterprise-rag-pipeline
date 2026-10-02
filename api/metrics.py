"""Prometheus metrics for Enterprise RAG API."""
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response


# Document metrics
DOCUMENTS_UPLOADED = Counter(
    "enterprise_rag_documents_uploaded_total",
    "Total documents uploaded",
    ["tenant_id", "status"]
)

DOCUMENTS_PROCESSED = Counter(
    "enterprise_rag_documents_processed_total",
    "Total documents processed by worker",
    ["tenant_id", "status"]
)

DOCUMENTS_DELETED = Counter(
    "enterprise_rag_documents_deleted_total",
    "Total documents deleted",
    ["tenant_id"]
)

DOCUMENTS_REINDEXED = Counter(
    "enterprise_rag_documents_reindexed_total",
    "Total documents reindexed",
    ["tenant_id", "status"]
)

# Search metrics
SEARCH_REQUESTS = Counter(
    "enterprise_rag_search_requests_total",
    "Total search requests",
    ["mode", "status"]
)

SEARCH_LATENCY = Histogram(
    "enterprise_rag_search_latency_seconds",
    "Search latency in seconds",
    ["mode"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

SEARCH_RESULTS_COUNT = Histogram(
    "enterprise_rag_search_results_count",
    "Number of results returned per search",
    ["mode"],
    buckets=[1, 5, 10, 25, 50, 100]
)

# Worker metrics
WORKER_PROCESSING_TIME = Histogram(
    "enterprise_rag_worker_processing_seconds",
    "Worker processing time by stage",
    ["stage"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
)

WORKER_MESSAGES_PROCESSED = Counter(
    "enterprise_rag_worker_messages_processed_total",
    "Total messages processed by worker",
    ["queue", "status"]
)

WORKER_ACTIVE = Gauge(
    "enterprise_rag_worker_active",
    "Number of active worker processes"
)

# Queue metrics
QUEUE_DEPTH = Gauge(
    "enterprise_rag_queue_depth",
    "Current queue depth",
    ["queue_name"]
)

QUEUE_MESSAGES_PUBLISHED = Counter(
    "enterprise_rag_queue_messages_published_total",
    "Total messages published to queues",
    ["queue_name"]
)

QUEUE_MESSAGES_CONSUMED = Counter(
    "enterprise_rag_queue_messages_consumed_total",
    "Total messages consumed from queues",
    ["queue_name", "status"]
)

# System metrics
API_REQUESTS_TOTAL = Counter(
    "enterprise_rag_api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"]
)

API_REQUEST_LATENCY = Histogram(
    "enterprise_rag_api_request_latency_seconds",
    "API request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

ACTIVE_CONNECTIONS = Gauge(
    "enterprise_rag_active_connections",
    "Number of active connections"
)

# Database metrics
DB_CONNECTIONS_ACTIVE = Gauge(
    "enterprise_rag_db_connections_active",
    "Active database connections"
)

DB_QUERY_LATENCY = Histogram(
    "enterprise_rag_db_query_latency_seconds",
    "Database query latency",
    ["query_type"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)

# MinIO metrics
MINIO_OPERATIONS = Counter(
    "enterprise_rag_minio_operations_total",
    "Total MinIO operations",
    ["operation", "status"]
)

MINIO_OPERATION_LATENCY = Histogram(
    "enterprise_rag_minio_operation_latency_seconds",
    "MinIO operation latency",
    ["operation"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Embedding metrics
EMBEDDING_GENERATION_TIME = Histogram(
    "enterprise_rag_embedding_generation_seconds",
    "Embedding generation time",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

EMBEDDING_DIMENSION = Gauge(
    "enterprise_rag_embedding_dimension",
    "Embedding vector dimension"
)


def get_metrics_response() -> Response:
    """Generate Prometheus metrics response."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Helper functions for recording metrics
def record_document_upload(tenant_id: str, status: str):
    DOCUMENTS_UPLOADED.labels(tenant_id=tenant_id, status=status).inc()

def record_document_processed(tenant_id: str, status: str):
    DOCUMENTS_PROCESSED.labels(tenant_id=tenant_id, status=status).inc()

def record_document_deleted(tenant_id: str):
    DOCUMENTS_DELETED.labels(tenant_id=tenant_id).inc()

def record_document_reindexed(tenant_id: str, status: str):
    DOCUMENTS_REINDEXED.labels(tenant_id=tenant_id, status=status).inc()

def record_search_request(mode: str, status: str, latency: float, results_count: int):
    SEARCH_REQUESTS.labels(mode=mode, status=status).inc()
    SEARCH_LATENCY.labels(mode=mode).observe(latency)
    SEARCH_RESULTS_COUNT.labels(mode=mode).observe(results_count)

def record_worker_processing(stage: str, duration: float):
    WORKER_PROCESSING_TIME.labels(stage=stage).observe(duration)

def record_worker_message(queue: str, status: str):
    WORKER_MESSAGES_PROCESSED.labels(queue=queue, status=status).inc()

def set_worker_active(count: int):
    WORKER_ACTIVE.set(count)

def set_queue_depth(queue_name: str, depth: int):
    QUEUE_DEPTH.labels(queue_name=queue_name).set(depth)

def record_queue_publish(queue_name: str):
    QUEUE_MESSAGES_PUBLISHED.labels(queue_name=queue_name).inc()

def record_queue_consume(queue_name: str, status: str):
    QUEUE_MESSAGES_CONSUMED.labels(queue_name=queue_name, status=status).inc()

def record_api_request(method: str, endpoint: str, status: int, latency: float):
    API_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=str(status)).inc()
    API_REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(latency)

def set_db_connections_active(count: int):
    DB_CONNECTIONS_ACTIVE.set(count)

def record_db_query(query_type: str, latency: float):
    DB_QUERY_LATENCY.labels(query_type=query_type).observe(latency)

def record_minio_operation(operation: str, status: str, latency: float):
    MINIO_OPERATIONS.labels(operation=operation, status=status).inc()
    MINIO_OPERATION_LATENCY.labels(operation=operation).observe(latency)

def record_embedding_generation(latency: float):
    EMBEDDING_GENERATION_TIME.observe(latency)

def set_embedding_dimension(dim: int):
    EMBEDDING_DIMENSION.set(dim)