"""Explicit dependencies shared by services and HTTP handlers.

The server entry point supplies this protocol; services never import server.
Tests can replace paths, queues and operations on that entry point.
"""
from typing import Any, Callable, Protocol
from pathlib import Path
from queue import Queue
from threading import Event
from typing import ContextManager


class ApplicationContext(Protocol):
    CANCEL: dict[tuple[str, str], Event]
    DATA: Path
    DEFAULT_SETTINGS: dict[str, Any]
    FILE_ACTION_LOCK: ContextManager[Any]
    JOBS: Queue[tuple[str, str]]
    LIBRARY: Path
    LOCK: ContextManager[Any]
    PORT: int
    QUEUED: set[tuple[str, str]]
    QUOTA_PAUSED: Event
    RESUME_REQUESTED: set[tuple[str, str]]
    ROOT: Path
    STATIC: Path
    STATUS_CACHE: dict[str, Any]
    SELECTION_REQUESTS: Any
    STRUCTURE_JOBS: Queue[str]
    STRUCTURE_QUEUED: set[str]
    TRANSLATION_FIELDS: tuple[str, ...]
    VERSION: str

    all_docs: Callable[..., Any]
    archive_translation: Callable[..., Any]
    archive_translation_draft: Callable[..., Any]
    batches: Callable[..., Any]
    codex_status: Callable[..., Any]
    commit_import_result: Callable[..., Any]
    crossref_metadata: Callable[..., Any]
    db: Callable[..., Any]
    enqueue: Callable[..., Any]
    enrich_publication: Callable[..., Any]
    execute_translation: Callable[..., Any]
    get_doc: Callable[..., Any]
    import_pdf: Callable[..., Any]
    init_db: Callable[..., Any]
    journal_abbr: Callable[..., Any]
    media_url: Callable[..., Any]
    now: Callable[..., Any]
    paper_file_action: Callable[..., Any]
    patch_paper_fields: Callable[..., Any]
    prepare_translation: Callable[..., Any]
    public_doc: Callable[..., Any]
    put_doc: Callable[..., Any]
    restore_translation: Callable[..., Any]
    restore_translation_draft: Callable[..., Any]
    schedule_structure: Callable[..., Any]
    settings: Callable[..., Any]
    structure_worker: Callable[..., Any]
    translation_counts: Callable[..., Any]
    translation_snapshot: Callable[..., Any]
    update_doc: Callable[..., Any]
    valid_ratio: Callable[..., Any]
    valid_year: Callable[..., Any]
    validate_reader_state: Callable[..., Any]
    validate_settings: Callable[..., Any]
    version_metadata: Callable[..., Any]
    worker: Callable[..., Any]
