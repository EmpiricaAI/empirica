import json
from argparse import Namespace
from unittest.mock import patch

from empirica.cli.command_handlers.project_search import handle_project_search_command


def test_project_search_resolves_project_name_before_qdrant_calls(capsys):
    args = Namespace(
        project_id="_config",
        task="workflow state model",
        type="focused",
        limit=5,
        global_search=False,
        output="json",
        verbose=False,
    )

    fake_results = {"docs": [{"doc_path": "docs/workflow/state-model.md", "score": 0.77}]}

    with (
        patch(
            "empirica.cli.utils.project_resolver.resolve_project_id",
            return_value="90148336-3464-4f13-a401-5d8f44e6657d",
        ) as resolve_mock,
        patch("empirica.core.qdrant.vector_store.init_collections") as init_mock,
        patch("empirica.core.qdrant.vector_store.search", return_value=fake_results) as search_mock,
    ):
        handle_project_search_command(args)

    resolve_mock.assert_called_once_with("_config")
    init_mock.assert_called_once_with("90148336-3464-4f13-a401-5d8f44e6657d")
    search_mock.assert_called_once_with(
        "90148336-3464-4f13-a401-5d8f44e6657d",
        "workflow state model",
        kind="focused",
        limit=5,
        include_resolved=False,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["results"] == fake_results
