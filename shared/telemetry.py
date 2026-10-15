"""JSON logs and W3C trace context; never log request bodies or credentials."""
import contextvars
import json
import logging
import re
import uuid
from datetime import datetime, timezone

trace_context = contextvars.ContextVar('traceparent', default='')


def new_trace(parent=None):
    if isinstance(parent, str) and re.fullmatch(r'00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]', parent):
        trace_id = parent.split('-')[1]
        if int(trace_id, 16) and int(parent.split('-')[2], 16):
            return f'00-{trace_id}-{uuid.uuid4().hex[:16]}-01'
    return f'00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01'


class JsonFormatter(logging.Formatter):
    def format(self, record):
        context = trace_context.get()
        return json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'level': record.levelname,
                           'logger': record.name, 'message': record.getMessage(),
                           'trace_id': context.split('-')[1] if context else None,
                           'span_id': context.split('-')[2] if context else None}, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.getLogger().handlers = [handler]
    logging.getLogger().setLevel(logging.INFO)
    # Transport debug logs can include authenticated URLs. Keep them quiet.
    for name in ('pika', 'aio_pika', 'aiormq', 'urllib3', 'httpx'):
        logging.getLogger(name).setLevel(logging.ERROR)
