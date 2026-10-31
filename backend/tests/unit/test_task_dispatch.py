"""Task dispatch keeps the inline and Celery paths behind one call site."""

from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from app.workers.dispatch import schedule, task_backend_info


def test_inline_backend_uses_fastapi_background_tasks():
    background = BackgroundTasks()
    used = schedule(background, "ingest", uuid4())

    assert used == "inline"
    assert len(background.tasks) == 1


def test_unknown_task_kind_is_rejected():
    with pytest.raises(ValueError):
        schedule(BackgroundTasks(), "nope", uuid4())


def test_backend_info_describes_configuration():
    info = task_backend_info()
    assert info["backend"] in {"inline", "celery"}
    assert info["inline_tasks"] == ["eval", "ingest"]
