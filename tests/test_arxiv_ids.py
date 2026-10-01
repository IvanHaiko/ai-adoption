from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq

from collector.arxiv_ids import parse_feed, resolve, valid_arxiv_id
from collector.fetch import Response
from collector.storage import read_gzip, read_json

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2501.12345v2</id><title>Paper</title></entry>
</feed>"""


class FakeHttp:
    def __init__(self):
        self.call_count = 0

    def get(self, url):
        self.call_count += 1
        return Response(url, 200, ATOM)


def test_exact_id_lookup_is_cached_and_preserves_original_reference(tmp_path):
    silver = tmp_path / "data/silver"
    silver.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [{"arxiv_id": "2501.12345"}, {"arxiv_id": "2501.99999"}, {"arxiv_id": "0000.00000"}]
        ),
        silver / "hf_arxiv_links.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "tags_json": json.dumps(
                        ["arxiv:2501.12345v2", "arxiv:2501.99999", "arxiv:0000.00000"]
                    )
                }
            ]
        ),
        silver / "hf_observations.parquet",
    )
    http = FakeHttp()
    first = resolve(tmp_path, http)
    assert first["status_counts"] == {
        "resolved": 1,
        "not_found": 1,
        "temporary_source_failure": 0,
        "malformed_id": 1,
    }
    assert http.call_count == 1
    manifest = read_json(tmp_path / "data/enrichment/arxiv_ids/_manifest.json")
    item = manifest["items"]["2501.12345"]
    assert item["original_references"] == ["2501.12345v2"]
    assert read_gzip(tmp_path / "data/enrichment/arxiv_ids" / item["response_file"]) == ATOM
    before = (tmp_path / "data/enrichment/arxiv_ids/_manifest.json").read_bytes()
    second = resolve(tmp_path, http)
    assert second["http_calls"] == 0 and http.call_count == 1
    assert (tmp_path / "data/enrichment/arxiv_ids/_manifest.json").read_bytes() == before


def test_atom_and_old_id_validation():
    assert parse_feed(ATOM, {"2501.12345"}) == {"2501.12345"}
    assert valid_arxiv_id("hep-th/9901001")
    assert valid_arxiv_id("2501.12345")
    assert not valid_arxiv_id("0000.00000")
    assert not valid_arxiv_id("2507.00000")
