"""Bounded read-only Workers AI Radar usage spike, with isolated license metadata."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode

from collector.fetch import HttpClient


def queries(start, end):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first > last or last >= dt.datetime.now(dt.timezone.utc).date():
        raise ValueError("Use increasing completed UTC dates")
    params = {"dateStart": start + "T00:00:00Z",
              "dateEnd": (last + dt.timedelta(days=1)).isoformat() + "T00:00:00Z",
              "format": "json"}
    for dimension in ("model", "task"):
        for mode in ("summary", "timeseries_groups"):
            query = dict(params)
            if dimension == "model":
                query["limitPerGroup"] = 100
            if mode == "timeseries_groups":
                query["aggInterval"] = "1d"
            yield f"{mode}_{dimension}", (
                f"https://api.cloudflare.com/client/v4/radar/ai/inference/{mode}/{dimension}?"
                + urlencode(query))


def collect(root, start, end, client=None):
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise ValueError("CLOUDFLARE_API_TOKEN is required")
    client = client or HttpClient(timeout=30, max_attempts=2)
    output = Path(root) / "data/external/cloudflare-radar" / f"{start}_{end}"
    records = []
    for label, url in queries(start, end):
        response = client.get(url, headers={"Authorization": "Bearer " + token})
        if not response.ok:
            records.append({"query": label, "url": url, "http_status": response.status})
            continue
        body = response.body
        digest = hashlib.sha256(body).hexdigest()
        path = output / label / digest
        path.mkdir(parents=True, exist_ok=True)
        payload_path = path / "response.json"
        if payload_path.exists():
            if payload_path.read_bytes() != body:
                raise ValueError("Existing archived bytes mismatch")
        else:
            payload_path.write_bytes(body)
        record = {"query": label, "url": url, "http_status": response.status,
                  "sha256": digest, "file": payload_path.relative_to(root).as_posix(),
                  "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "license": "CC-BY-NC-4.0", "success": json.loads(body).get("success")}
        manifest_path = path / "_manifest.json"
        if not manifest_path.exists():
            manifest_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        records.append(record)
    return records


def normalize_timeseries(payload, start, end):
    result = payload["result"]
    if not payload.get("success") or result["meta"].get("normalization") != "PERCENTAGE":
        raise ValueError("Expected successful percentage-normalized response")
    if result["meta"].get("aggInterval") != "ONE_DAY":
        raise ValueError("Expected daily aggregation")
    serie = result["serie_0"]
    timestamps = serie["timestamps"]
    expected = {(dt.date.fromisoformat(start) + dt.timedelta(days=i)).isoformat()
                for i in range((dt.date.fromisoformat(end)-dt.date.fromisoformat(start)).days+1)}
    keys = [key for key in serie if key != "timestamps"]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("Duplicate dates")
    if any(len(serie[key]) != len(timestamps) for key in keys):
        raise ValueError("Unequal series lengths")
    rows = []
    for i, timestamp in enumerate(timestamps):
        if timestamp[:10] not in expected:
            continue  # API dateEnd is inclusive: retain raw extra boundary bucket only.
        values = [float(serie[key][i]) for key in keys]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Invalid percentages")
        if abs(sum(values) - 100) > 0.001:
            raise ValueError("Incomplete daily denominator")
        rows.extend({"date": timestamp[:10], "source_entity": key, "share_percent": value}
                    for key, value in zip(keys, values, strict=True))
    if {row["date"] for row in rows} != expected:
        raise ValueError("Missing requested dates")
    return rows, len(timestamps)-len(expected)


def analyze(root, start, end):
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = Path(root)
    selected = {}
    for path in (root / "data/external/cloudflare-radar" / f"{start}_{end}").rglob(
            "_manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        label = manifest["query"]
        if label.endswith("model") and "limitPerGroup=100" not in manifest["url"]:
            continue
        if label not in selected or manifest["fetched_at"] > selected[label][0]["fetched_at"]:
            selected[label] = manifest, path
    summary, all_rows = {}, []
    for label, (manifest, path) in sorted(selected.items()):
        raw = (path.parent / "response.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
            raise ValueError("Archive checksum mismatch")
        payload = json.loads(raw)
        result = payload["result"]
        if label.startswith("timeseries"):
            rows, excluded = normalize_timeseries(payload, start, end)
            summary[label] = {"days": len({r["date"] for r in rows}), "rows": len(rows),
                              "entities": len({r["source_entity"] for r in rows}),
                              "excluded_boundary_buckets": excluded,
                              "meta": result["meta"], "provenance": manifest}
            for row in rows:
                row.update(dimension=label.rsplit("_", 1)[1],
                           source_sha256=manifest["sha256"], license="CC-BY-NC-4.0")
            all_rows.extend(rows)
        else:
            summary[label] = {"shares": result["summary_0"], "meta": result["meta"],
                              "provenance": manifest,
                              "scope_note": "Inclusive end boundary; use daily rows for comparison"}
    out = root / "data/analytics"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(all_rows), out / "cloudflare_radar_usage.parquet")
    (out / "cloudflare_radar_quality.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    return {key: {k: v for k, v in value.items() if k not in ("shares", "meta", "provenance")}
            for key, value in summary.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--start", default="2026-10-16")
    parser.add_argument("--end", default="2026-11-14")
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()
    if not args.analyze_only:
        print(json.dumps(collect(args.root, args.start, args.end), indent=2))
    print(json.dumps(analyze(args.root, args.start, args.end), indent=2))


if __name__ == "__main__":
    main()
