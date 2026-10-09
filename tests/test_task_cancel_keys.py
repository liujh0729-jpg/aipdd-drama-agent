import pytest

from novelvideo.task_backend.cancel import cancel_key

pytestmark = pytest.mark.m07


def test_cancel_key_is_scoped_to_single_task_run():
    first = cancel_key(
        project_id="proj",
        task_type="stage_asset",
        episode=0,
        scope="stage_asset__abc",
        task_id="run-1",
    )
    second = cancel_key(
        project_id="proj",
        task_type="stage_asset",
        episode=0,
        scope="stage_asset__abc",
        task_id="run-2",
    )

    assert first != second
    assert first.endswith(":run-1")
    assert second.endswith(":run-2")
