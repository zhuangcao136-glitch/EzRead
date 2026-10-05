"""Load the executable workflow from the project's paper-import skill."""
import importlib.util
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent / 'skills' / 'ezread-paper-import'
_spec = importlib.util.spec_from_file_location('ezread_import_workflow', SKILL_DIR / 'scripts' / 'organize.py')
_workflow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_workflow)
VERSION = _workflow.VERSION
organize = _workflow.organize
fingerprint = _workflow.fingerprint
audit_structure = _workflow.audit_structure
validate_roles = _workflow.validate_roles
refine = _workflow.refine
selection_source = _workflow.selection_source
PENDING_COLLECTION = _workflow.PENDING_COLLECTION
collection_context = _workflow.collection_context
validate_collection = _workflow.validate_collection
