"""Public status contract, without fabricated posting payloads."""

from waterlooworks_flash_next.api_models import PostingAnalysisResponse


def test_analysis_can_identify_current_validated_model_output() -> None:
    schema = PostingAnalysisResponse.model_json_schema()
    assert "available" in schema["properties"]["model_status"]["enum"]
    assert "model_producer" in schema["properties"]


def test_explicit_inference_selection_supports_a_saved_workspace() -> None:
    from waterlooworks_flash_next.inference import MAX_JOB_POSTINGS

    assert 1218 <= MAX_JOB_POSTINGS <= 10000
