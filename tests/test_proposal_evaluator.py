from agents import docs_controller, proposal_evaluator


def test_evaluator_consumes_docs_state(fake_repo, state_dir):
    docs_controller.run(fake_repo, state_dir)
    result = proposal_evaluator.run(fake_repo, state_dir)
    assert result["status"] == "completed"


def test_evaluator_not_applicable_without_proposal_docs(fake_repo, state_dir):
    docs_controller.run(fake_repo, state_dir)  # fake repo has no 04_proposal/*.md in manifest
    result = proposal_evaluator.run(fake_repo, state_dir)
    assert result["overall_status"] == "NOT_APPLICABLE_YET"
    assert result["score"] is None


def test_evaluator_score_always_null_even_when_activated(fake_repo, state_dir):
    # Add proposal docs to the manifest and mark them DRAFT to activate.
    import json
    manifest_path = fake_repo / "config" / "canonical_documents.json"
    manifest = json.loads(manifest_path.read_text())
    for name in ["EXCELLENCE", "IMPACT", "IMPLEMENTATION"]:
        manifest["documents"].append({
            "path": f"04_proposal/{name}.md", "title": name,
            "required_phase": "baseline", "required": True,
        })
        (fake_repo / "04_proposal" / f"{name}.md").write_text(
            "<!-- CANONICAL_STATUS: DRAFT -->\ntext\n"
        )
    manifest_path.write_text(json.dumps(manifest))

    docs_controller.run(fake_repo, state_dir)
    result = proposal_evaluator.run(fake_repo, state_dir)

    assert result["overall_status"] == "ACTIVE_DIAGNOSTIC_MODE"
    assert result["score"] is None  # never 0, never a fabricated number
    assert any(f["id"] == "scoring-not-yet-implemented" for f in result["findings"])


def test_evaluator_missing_docs_state_reports_unavailable_reason(fake_repo, state_dir):
    result = proposal_evaluator.run(fake_repo, state_dir)  # docs_controller never ran
    assert result["overall_status"] == "NOT_APPLICABLE_YET"
    assert "has not run yet" in result["reason"]
