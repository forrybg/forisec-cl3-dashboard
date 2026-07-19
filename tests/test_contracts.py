import json
from pathlib import Path

import jsonschema
import pytest

from agents import docs_controller, proposal_evaluator, repository_guardian, project_supervisor

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"


def _schema(name):
    return json.loads((CONTRACTS_DIR / name).read_text())


def test_docs_state_validates(fake_repo, state_dir):
    result = docs_controller.run(fake_repo, state_dir)
    jsonschema.validate(result, _schema("docs_state.schema.json"))


def test_evaluation_state_validates(fake_repo, state_dir):
    docs_controller.run(fake_repo, state_dir)
    result = proposal_evaluator.run(fake_repo, state_dir)
    jsonschema.validate(result, _schema("evaluation_state.schema.json"))


def test_guardian_state_validates(fake_repo, state_dir):
    result = repository_guardian.run(fake_repo, state_dir)
    jsonschema.validate(result, _schema("guardian_state.schema.json"))


def test_supervisor_state_validates(fake_repo, state_dir):
    docs_controller.run(fake_repo, state_dir)
    repository_guardian.run(fake_repo, state_dir)
    proposal_evaluator.run(fake_repo, state_dir)
    result = project_supervisor.run(fake_repo, state_dir)
    jsonschema.validate(result, _schema("supervisor_state.schema.json"))


def test_schema_version_required_in_all_four(fake_repo, state_dir):
    docs_controller.run(fake_repo, state_dir)
    repository_guardian.run(fake_repo, state_dir)
    proposal_evaluator.run(fake_repo, state_dir)
    supervisor_result = project_supervisor.run(fake_repo, state_dir)
    for f in ["docs_state.json", "guardian_state.json", "evaluation_state.json", "supervisor_state.json"]:
        data = json.loads((state_dir / f).read_text())
        assert "schema_version" in data


def test_malformed_json_rejected_by_supervisor(fake_repo, state_dir):
    (state_dir / "guardian_state.json").write_text("{not valid json")
    result = project_supervisor.run(fake_repo, state_dir)
    assert result["overall_status"] == "DEGRADED"
    assert result["state_files"]["guardian_state.json"]["status"] == "INVALID"
