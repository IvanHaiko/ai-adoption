"""Rule 1: the loader stores what arrived. The envelope around each HuggingFace
response is added, but the response bytes inside it are untouched."""
from __future__ import annotations

import json

from collector import paths
from collector.run import collect
from collector.sources.hf_models import wanted_ids
from collector.storage import read_gzip
from tests.conftest import catalogue

DATE = "2026-08-29"


def test_openrouter_payload_is_stored_byte_for_byte(tmp_path, http, clock):
    collect(tmp_path, snapshot_date=DATE, http=http, now=clock)
    stored = read_gzip(paths.leg_path(tmp_path, DATE, "openrouter_models"))
    assert stored == catalogue(http.entries)


def test_hf_body_survives_the_envelope_unmodified(tmp_path, http, clock):
    collect(tmp_path, snapshot_date=DATE, http=http, now=clock)
    lines = read_gzip(paths.leg_path(tmp_path, DATE, "hf_models")).splitlines()
    record = json.loads(lines[0])
    original = json.dumps({"id": "Vendor/Alpha", "downloads": 1000, "likes": 7}).encode()
    assert json.dumps(record["body"], separators=(", ", ": ")).encode() == original
    assert record["hf_id"] == "Vendor/Alpha"
    assert record["http_status"] == 200


def test_empty_hugging_face_id_is_not_a_repository():
    """The field is present on every OpenRouter model and empty on most; a
    collector that tests for presence would ask HuggingFace about nothing."""
    payload = catalogue([("a", "Vendor/A"), ("b", ""), ("c", "Vendor/A")])
    assert wanted_ids(payload) == ["Vendor/A"]
