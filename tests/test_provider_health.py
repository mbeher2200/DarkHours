"""provider_health: the detail recorded for AWS SDK failures is the error code only."""
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from darkhours import provider_health as _ph

_MESSAGE_TEXT = "FULL-SDK-MESSAGE-TEXT"


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": _MESSAGE_TEXT}},
        "SearchPlaceIndexForText",
    )


@pytest.fixture(autouse=True)
def _fresh_records(monkeypatch):
    monkeypatch.setattr(_ph, "_records", {})


@pytest.fixture(autouse=True)
def _reset_location_client():
    import darkhours.darksky as _darksky
    _darksky._reset_location_client()
    yield
    _darksky._reset_location_client()


def test_aws_error_detail_client_error_is_code():
    assert _ph.aws_error_detail(_client_error("ThrottlingException")) == "ThrottlingException"


def test_aws_error_detail_non_client_error_is_class_name():
    e = EndpointConnectionError(endpoint_url="https://example.invalid")
    assert _ph.aws_error_detail(e) == "EndpointConnectionError"
    assert _ph.aws_error_detail(RuntimeError(_MESSAGE_TEXT)) == "RuntimeError"


def test_forward_geocode_failure_records_code_only(monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-index")
    mock_client = MagicMock()
    mock_client.search_place_index_for_text.side_effect = _client_error("AccessDeniedException")
    with patch("boto3.client", return_value=mock_client):
        from darkhours.location import _geocode_via_aws
        with pytest.raises(RuntimeError):
            _geocode_via_aws("Denver", "Denver")
    rec = _ph.snapshot()["aws_location"]
    assert rec == {"status": "error", "detail": "AccessDeniedException"}


def test_suggest_failure_records_code_only(monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-index")
    mock_client = MagicMock()
    mock_client.search_place_index_for_suggestions.side_effect = _client_error("ValidationException")
    with patch("boto3.client", return_value=mock_client):
        from darkhours.location import _suggest_via_aws
        with pytest.raises(RuntimeError):
            _suggest_via_aws("Den", 5)
    assert _ph.snapshot()["aws_location"] == {"status": "error", "detail": "ValidationException"}


def test_reverse_geocode_failure_records_code_only():
    mock_client = MagicMock()
    mock_client.search_place_index_for_position.side_effect = _client_error("ResourceNotFoundException")
    with patch("boto3.client", return_value=mock_client), \
         patch("darkhours.darksky.cache") as mock_cache:
        mock_cache.get.return_value = None
        from darkhours.darksky import _aws_location_settlement
        assert _aws_location_settlement(39.7392, -104.9903) is None
    assert _ph.snapshot()["aws_location"] == {"status": "error", "detail": "ResourceNotFoundException"}


def test_drive_time_failure_records_code_only(monkeypatch):
    import darkhours.darksky as ds
    monkeypatch.setattr("darkhours.darksky.cache.get", lambda k: None)
    monkeypatch.setattr("darkhours.darksky.cache.set", lambda *a, **kw: None)
    client = MagicMock()
    client.calculate_routes.side_effect = _client_error("ThrottlingException")
    monkeypatch.setattr(ds, "_georoutes", lambda: client)
    clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True}]
    ds._aws_drive_times(35.2, -111.6, clusters)
    assert _ph.snapshot()["aws_georoutes"] == {"status": "error", "detail": "ThrottlingException"}
