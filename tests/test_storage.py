import pytest

from spec_agent.config import Settings
from spec_agent.services.workflow import Workflow


def test_single_process_directory_lock(tmp_path):
    first = Workflow(Settings(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="already running"):
            Workflow(Settings(tmp_path))
    finally:
        first.close()
    second = Workflow(Settings(tmp_path))
    second.close()


def test_bad_font_does_not_lock_store(tmp_path):
    with pytest.raises(ValueError, match="font"):
        Workflow(Settings(tmp_path, tmp_path / "missing.ttf"))
    workflow = Workflow(Settings(tmp_path))
    workflow.close()
