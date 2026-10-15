from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry

REGISTRY = CollectorRegistry()
MESSAGES = Counter('enterprise_rag_worker_messages_processed_total', 'Worker delivery outcomes', ['status'], registry=REGISTRY)
STAGE_TIME = Histogram('enterprise_rag_worker_processing_seconds', 'Processing time by stage', ['stage'], registry=REGISTRY)
CHUNKS = Counter('enterprise_rag_worker_chunks_total', 'Chunks indexed', registry=REGISTRY)
ACTIVE = Gauge('enterprise_rag_worker_active', 'Broker consumer connected', registry=REGISTRY)
