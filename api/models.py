"""Compatibility exports; schema has one source of truth."""

from shared.models import Base, Chunk, Document, OutboxEvent

__all__ = ["Base", "Document", "Chunk", "OutboxEvent"]
