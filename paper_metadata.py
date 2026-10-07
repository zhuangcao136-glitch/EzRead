"""Load publication extraction from the maintained EzRead import skill."""
import importlib.util
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent / 'skills' / 'ezread-paper-import'
_spec = importlib.util.spec_from_file_location('ezread_publication_workflow', SKILL_DIR / 'scripts' / 'publication.py')
_workflow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_workflow)
extract = _workflow.extract
missing_fields = _workflow.missing_fields
PUBLICATION_FIELDS = _workflow.PUBLICATION_FIELDS
valid_authors = _workflow.valid_authors
clean_venue = _workflow.clean_venue
is_conference_venue = _workflow.is_conference_venue
conference_abbreviation = _workflow.conference_abbreviation
normalize_publication = _workflow.normalize_publication
title_evidence = _workflow.title_evidence
