"""Build conservative Parquet Silver tables from all available Bronze days."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.parquet as pq

from collector.audit import snapshot_days
from collector.storage import read_gzip, read_json, sha256

ARXIV_ID = re.compile(r"^(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$", re.I)
ARXIV_TAG = re.compile(r"^arxiv:(.+)$", re.I)
ARXIV_NS = {"o": "http://www.openarchives.org/OAI/2.0/", "a": "http://arxiv.org/OAI/arXiv/"}
HF_LEGS = ("hf_models", "hf_top_models", "hf_new_models")
PRECEDENCE = {"hf_models": 0, "hf_top_models": 1, "hf_new_models": 2}

SCHEMAS = {
    "hf_observations": [
        "snapshot_date",
        "fetched_at",
        "source_leg",
        "hf_repo_id",
        "author",
        "created_at",
        "last_modified",
        "downloads_30d",
        "likes",
        "pipeline_tag",
        "library_name",
        "private",
        "gated",
        "disabled",
        "base_model",
        "arxiv_ids",
        "tags_json",
        "first_seen_date",
        "first_seen_source",
        "age_at_first_seen_days",
        "source_snapshot",
        "source_url",
    ],
    "hf_repositories": [
        "snapshot_date",
        "fetched_at",
        "source_leg",
        "hf_repo_id",
        "author",
        "created_at",
        "last_modified",
        "downloads_30d",
        "likes",
        "pipeline_tag",
        "library_name",
        "private",
        "gated",
        "disabled",
        "base_model",
        "arxiv_ids",
        "tags_json",
        "first_seen_date",
        "first_seen_source",
        "age_at_first_seen_days",
        "source_snapshot",
        "source_url",
    ],
    "openrouter_models": [
        "snapshot_date",
        "fetched_at",
        "openrouter_model_id",
        "canonical_slug",
        "alias_target",
        "is_alias",
        "name",
        "provider",
        "hugging_face_id",
        "created_at",
        "context_length",
        "pricing_prompt",
        "pricing_completion",
        "architecture_modality",
        "first_seen_date",
        "first_seen_source",
        "age_at_first_seen_days",
        "source_snapshot",
    ],
    "arxiv_papers": [
        "snapshot_date",
        "fetched_at",
        "arxiv_id",
        "title",
        "abstract",
        "authors_json",
        "submitted_at",
        "updated_at",
        "oai_datestamp",
        "categories_json",
        "primary_category",
        "doi",
        "journal_ref",
        "first_seen_date",
        "source_snapshot",
        "source_url",
        "deleted",
        "metadata_source",
    ],
    "model_platform_links": [
        "source_entity",
        "target_entity",
        "relationship_type",
        "match_method",
        "match_confidence",
        "first_seen_date",
        "last_seen_date",
    ],
    "arxiv_id_resolutions": [
        "arxiv_id",
        "original_references_json",
        "resolution_status",
        "source_interface",
        "fetched_at",
        "source_url",
        "source_snapshot",
        "sha256_raw",
        "error",
    ],
    "hf_arxiv_links": [
        "hf_repo_id",
        "arxiv_id",
        "match_method",
        "confidence",
        "evidence",
        "first_seen_date",
        "last_seen_date",
        "resolution_status",
    ],
    "hf_repo_cohorts": [
        "hf_repo_id",
        "created_at",
        "first_seen_date",
        "cohort_month",
        "cohort_week",
        "first_seen_source",
        "age_at_first_seen_days",
        "initial_downloads_30d",
        "initial_likes",
        "base_model",
        "arxiv_link_present",
        "openrouter_link_present",
    ],
    "hf_provider_model_observations": [
        "snapshot_date", "fetched_at", "hf_repo_id", "provider_count",
        "first_observed_provider_catalogue_date", "source_snapshot", "source_url",
    ],
    "hf_inference_provider_observations": [
        "snapshot_date", "fetched_at", "hf_repo_id", "provider_name",
        "provider_model_id", "task", "status", "first_seen_provider_date",
        "source_snapshot", "source_url", "raw_mapping_json", "parser_version",
    ],
}
BOOLEAN_FIELDS = {
    "private",
    "disabled",
    "is_alias",
    "deleted",
    "arxiv_link_present",
    "openrouter_link_present",
}
INTEGER_FIELDS = {
    "downloads_30d",
    "likes",
    "context_length",
    "age_at_first_seen_days",
    "initial_downloads_30d",
    "initial_likes",
    "provider_count",
}


def iso(value, errors: list, where: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, int | float):
            return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.isoformat()
    except (ValueError, TypeError, OverflowError):
        errors.append(f"{where}: invalid timestamp {value!r}")
        return None


def canonical_arxiv(value: str) -> str | None:
    value = value.strip().removeprefix("arXiv:").removeprefix("arxiv:")
    if not ARXIV_ID.fullmatch(value):
        return None
    return re.sub(r"v\d+$", "", value, flags=re.I)


def age_days(created: str | None, first_seen: str) -> int | None:
    if not created:
        return None
    return (dt.date.fromisoformat(first_seen) - dt.date.fromisoformat(created[:10])).days


def _jsonl(path: Path):
    for number, line in enumerate(read_gzip(path).splitlines(), 1):
        if line.strip():
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc


def _hf(day: str, leg: str, path: Path, errors: list) -> list[dict]:
    result = []
    for envelope in _jsonl(path):
        bodies = envelope.get("body")
        if leg == "hf_models":
            bodies = [bodies]
        if not isinstance(bodies, list):
            errors.append(f"{path}: body is not a list")
            continue
        for body in bodies:
            if not isinstance(body, dict):
                errors.append(f"{path}: non-object HF row")
                continue
            repo_id = body.get("id") or body.get("modelId") or envelope.get("hf_id")
            if not repo_id:
                errors.append(f"{path}: missing HF repo ID")
                continue
            tags = body.get("tags") or []
            if not isinstance(tags, list):
                errors.append(f"{path}: invalid tags for {repo_id}")
                tags = []
            arxiv_ids = sorted(
                {
                    canonical_arxiv(m.group(1))
                    for tag in tags
                    if isinstance(tag, str) and (m := ARXIV_TAG.match(tag))
                    if canonical_arxiv(m.group(1))
                }
            )
            base_models = [
                tag.removeprefix("base_model:")
                for tag in tags
                if isinstance(tag, str)
                and tag.startswith("base_model:")
                and not tag.startswith("base_model:finetune:")
            ]
            downloads, likes = body.get("downloads"), body.get("likes")
            for field, value in (("downloads", downloads), ("likes", likes)):
                if value is not None and (not isinstance(value, int) or value < 0):
                    errors.append(f"{path}: invalid {field} for {repo_id}: {value!r}")
            result.append(
                {
                    "snapshot_date": day,
                    "fetched_at": iso(envelope.get("fetched_at"), errors, str(path)),
                    "source_leg": leg,
                    "hf_repo_id": repo_id,
                    "author": body.get("author") or repo_id.split("/")[0],
                    "created_at": iso(body.get("createdAt"), errors, str(path)),
                    "last_modified": iso(body.get("lastModified"), errors, str(path)),
                    "downloads_30d": downloads,
                    "likes": likes,
                    "pipeline_tag": body.get("pipeline_tag"),
                    "library_name": body.get("library_name"),
                    "private": body.get("private"),
                    "gated": str(body.get("gated")) if body.get("gated") is not None else None,
                    "disabled": body.get("disabled"),
                    "base_model": base_models[0] if base_models else None,
                    "arxiv_ids": json.dumps(arxiv_ids),
                    "tags_json": json.dumps(tags),
                    "source_snapshot": str(path.relative_to(path.parents[3])).replace("\\", "/"),
                    "source_url": envelope.get("url")
                    or ("https://huggingface.co/api/models/" + repo_id),
                }
            )
    return result


def _hf_providers(day: str, path: Path, errors: list) -> tuple[list[dict], list[dict]]:
    models, providers = [], []
    source_path = str(path.relative_to(path.parents[3])).replace("\\", "/")
    for envelope in _jsonl(path):
        body = envelope.get("body")
        if not isinstance(body, list):
            errors.append(f"{path}: provider list body is not an array")
            continue
        fetched = iso(envelope.get("fetched_at"), errors, str(path))
        for model in body:
            if not isinstance(model, dict) or not model.get("id"):
                errors.append(f"{path}: provider model missing id")
                continue
            repo_id = model["id"]
            mappings = model.get("inferenceProviderMapping")
            if not isinstance(mappings, list):
                errors.append(f"{path}: {repo_id} has no provider mapping array")
                continue
            common = {
                "snapshot_date": day, "fetched_at": fetched, "hf_repo_id": repo_id,
                "source_snapshot": source_path, "source_url": envelope.get("url"),
            }
            models.append(common | {"provider_count": len(mappings)})
            for mapping in mappings:
                if not isinstance(mapping, dict) or not mapping.get("provider"):
                    errors.append(f"{path}: {repo_id} has malformed provider mapping")
                    continue
                providers.append(common | {
                    "provider_name": mapping["provider"],
                    "provider_model_id": mapping.get("providerId"),
                    "task": mapping.get("task"), "status": mapping.get("status"),
                    "raw_mapping_json": json.dumps(mapping, sort_keys=True, ensure_ascii=False),
                    "parser_version": "1",
                })
    return models, providers


def _openrouter(day: str, path: Path, fetched_at: str | None, errors: list) -> list[dict]:
    data = json.loads(read_gzip(path)).get("data")
    if not isinstance(data, list):
        raise ValueError(f"{path}: catalogue data is not a list")
    rows = []
    for model in data:
        mid = model.get("id")
        if not mid:
            errors.append(f"{path}: missing OpenRouter model ID")
            continue
        canonical = model.get("canonical_slug") or mid
        hf_id = model.get("hugging_face_id") or None
        if isinstance(hf_id, str):
            hf_id = hf_id.strip() or None
        pricing = model.get("pricing") or {}
        architecture = model.get("architecture") or {}
        rows.append(
            {
                "snapshot_date": day,
                "fetched_at": fetched_at,
                "openrouter_model_id": mid,
                "canonical_slug": canonical,
                "alias_target": canonical if canonical != mid else None,
                "is_alias": canonical != mid,
                "name": model.get("name"),
                "provider": mid.split("/")[0],
                "hugging_face_id": hf_id,
                "created_at": iso(model.get("created"), errors, str(path)),
                "context_length": model.get("context_length"),
                "pricing_prompt": str(pricing.get("prompt"))
                if pricing.get("prompt") is not None
                else None,
                "pricing_completion": str(pricing.get("completion"))
                if pricing.get("completion") is not None
                else None,
                "architecture_modality": architecture.get("modality"),
                "source_snapshot": str(path.relative_to(path.parents[3])).replace("\\", "/"),
            }
        )
    return rows


def _arxiv(day: str, path: Path, errors: list, root_dir: Path | None = None) -> list[dict]:
    rows = []
    for page in _jsonl(path):
        try:
            root = ET.fromstring(base64.b64decode(page["body_base64"], validate=True))
        except (KeyError, ValueError, ET.ParseError) as exc:
            errors.append(f"{path}: invalid arXiv XML page: {exc}")
            continue
        for record in root.findall("o:ListRecords/o:record", ARXIV_NS):
            header = record.find("o:header", ARXIV_NS)
            metadata = record.find("o:metadata/a:arXiv", ARXIV_NS)
            identifier = (
                header.findtext("o:identifier", default="", namespaces=ARXIV_NS)
                if header is not None
                else ""
            )
            aid = canonical_arxiv(identifier.removeprefix("oai:arXiv.org:"))
            if not aid:
                errors.append(f"{path}: invalid arXiv identifier {identifier!r}")
                continue
            deleted = header is not None and header.attrib.get("status") == "deleted"
            if metadata is None and not deleted:
                errors.append(f"{path}: {aid} missing metadata")
                continue

            def field(name, metadata=metadata):
                return (
                    metadata.findtext("a:" + name, namespaces=ARXIV_NS)
                    if metadata is not None
                    else None
                )

            authors = []
            if metadata is not None:
                for author in metadata.findall("a:authors/a:author", ARXIV_NS):
                    authors.append(
                        " ".join(
                            x
                            for x in [
                                author.findtext("a:forenames", namespaces=ARXIV_NS),
                                author.findtext("a:keyname", namespaces=ARXIV_NS),
                            ]
                            if x
                        )
                    )
            categories = (field("categories") or "").split()
            if not deleted and not categories:
                errors.append(f"{path}: {aid} has no categories")
            for category in categories:
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", category):
                    errors.append(f"{path}: {aid} invalid category {category!r}")
            datestamp = header.findtext("o:datestamp", namespaces=ARXIV_NS)
            if not datestamp:
                errors.append(f"{path}: {aid} missing OAI datestamp")
            else:
                iso(datestamp, errors, str(path))
            rows.append(
                {
                    "snapshot_date": day,
                    "fetched_at": page.get("fetched_at"),
                    "arxiv_id": aid,
                    "title": field("title"),
                    "abstract": field("abstract"),
                    "authors_json": json.dumps(authors),
                    "submitted_at": iso(field("created"), errors, str(path)),
                    "updated_at": iso(field("updated"), errors, str(path)),
                    "oai_datestamp": datestamp,
                    "categories_json": json.dumps(categories),
                    "primary_category": categories[0] if categories else None,
                    "doi": field("doi"),
                    "journal_ref": field("journal-ref"),
                    "source_snapshot": str(path.relative_to(root_dir or path.parents[3])).replace(
                        "\\", "/"
                    ),
                    "source_url": page.get("url"),
                    "deleted": deleted,
                    "metadata_source": "arxiv_oai_pmh",
                }
            )
    return rows


def _atom_enrichment(root: Path, errors: list) -> tuple[list[dict], list[dict]]:
    folder = root / "data/enrichment/arxiv_ids"
    manifest_path = folder / "_manifest.json"
    if not manifest_path.exists():
        return [], []
    manifest = read_json(manifest_path)
    entries = {}
    for filename in sorted(
        {
            item["response_file"]
            for item in manifest["items"].values()
            if item.get("status") in ("resolved", "not_found")
        }
    ):
        path = folder / filename
        raw = read_gzip(path)
        tree = ET.fromstring(raw)
        if tree.tag != "{http://www.w3.org/2005/Atom}feed":
            errors.append(f"{path}: not an Atom feed")
            continue
        for entry in tree.findall("{http://www.w3.org/2005/Atom}entry"):
            url = entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
            aid = canonical_arxiv(urlparse(url).path.removeprefix("/abs/"))
            if not aid:
                errors.append(f"{path}: invalid Atom entry ID {url!r}")
                continue
            entries[aid] = (entry, path, sha256(raw))
    a = "{http://www.w3.org/2005/Atom}"
    ax = "{http://arxiv.org/schemas/atom}"
    papers, statuses = [], []
    for aid, item in sorted(manifest["items"].items()):
        status = item["status"]
        path_name = item.get("response_file")
        statuses.append(
            {
                "arxiv_id": aid,
                "original_references_json": json.dumps(item.get("original_references", [])),
                "resolution_status": status,
                "source_interface": item.get("source_interface"),
                "fetched_at": item.get("fetched_at"),
                "source_url": item.get("request_url"),
                "source_snapshot": f"data/enrichment/arxiv_ids/{path_name}" if path_name else None,
                "sha256_raw": item.get("sha256_raw"),
                "error": item.get("error"),
            }
        )
        if status != "resolved":
            continue
        if aid not in entries:
            errors.append(f"{aid}: marked resolved but absent from stored Atom response")
            continue
        entry, path, actual_hash = entries[aid]
        if actual_hash != item.get("sha256_raw"):
            errors.append(f"{aid}: Atom response hash mismatch")
        categories = [
            x.attrib.get("term") for x in entry.findall(a + "category") if x.attrib.get("term")
        ]
        primary = entry.find(ax + "primary_category")
        authors = [x.findtext(a + "name") for x in entry.findall(a + "author")]
        fetched_at = item.get("fetched_at")
        papers.append(
            {
                "snapshot_date": fetched_at[:10],
                "fetched_at": fetched_at,
                "arxiv_id": aid,
                "title": entry.findtext(a + "title"),
                "abstract": entry.findtext(a + "summary"),
                "authors_json": json.dumps([x for x in authors if x]),
                "submitted_at": iso(entry.findtext(a + "published"), errors, str(path)),
                "updated_at": iso(entry.findtext(a + "updated"), errors, str(path)),
                "oai_datestamp": None,
                "categories_json": json.dumps(categories),
                "primary_category": primary.attrib.get("term") if primary is not None else None,
                "doi": entry.findtext(ax + "doi"),
                "journal_ref": entry.findtext(ax + "journal_ref"),
                "source_snapshot": str(path.relative_to(root)).replace("\\", "/"),
                "source_url": item.get("request_url"),
                "deleted": False,
                "metadata_source": "arxiv_atom_api_id_list",
            }
        )
    return papers, statuses


def _first_seen(rows: list[dict], id_field: str, source_field: str | None = None) -> None:
    first = {}
    for row in rows:
        key = row[id_field]
        candidate = (
            row["snapshot_date"],
            PRECEDENCE.get(row.get(source_field), 0) if source_field else 0,
            row.get(source_field) if source_field else "arxiv",
        )
        if key not in first or candidate < first[key]:
            first[key] = candidate
    for row in rows:
        day, _, source = first[row[id_field]]
        row["first_seen_date"] = day
        if source_field:
            row["first_seen_source"] = source
            row["age_at_first_seen_days"] = age_days(row.get("created_at"), day)


def _links(rows, source_key, target_key, relationship, method, confidence):
    links = {}
    for row in rows:
        target = row.get(target_key)
        if not target:
            continue
        key = (row[source_key], target)
        span = links.setdefault(key, [row["snapshot_date"], row["snapshot_date"]])
        span[0] = min(span[0], row["snapshot_date"])
        span[1] = max(span[1], row["snapshot_date"])
    return [
        {
            "source_entity": a,
            "target_entity": b,
            "relationship_type": relationship,
            "match_method": method,
            "match_confidence": confidence,
            "first_seen_date": span[0],
            "last_seen_date": span[1],
        }
        for (a, b), span in sorted(links.items())
    ]


def _write(path: Path, name: str, rows: list[dict]) -> None:
    cols = SCHEMAS[name]
    schema = pa.schema(
        [
            pa.field(
                k,
                pa.bool_()
                if k in BOOLEAN_FIELDS
                else pa.int64()
                if k in INTEGER_FIELDS
                else pa.string(),
            )
            for k in cols
        ]
    )
    table = pa.Table.from_pylist([{k: row.get(k) for k in cols} for row in rows], schema=schema)
    pq.write_table(table, path / (name + ".parquet"), compression="zstd")


def build(root: Path, output: Path | None = None) -> dict:
    output = output or root / "data" / "silver"
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    hf, router, arxiv = [], [], []
    provider_models, provider_observations = [], []
    days = snapshot_days(root)
    for day in days:
        folder = root / "data" / "raw" / day
        manifest = read_json(folder / "_manifest.json")
        for leg in HF_LEGS:
            if manifest.get("legs", {}).get(leg, {}).get("status") == "ok":
                hf.extend(_hf(day, leg, folder / (leg + ".jsonl.gz"), errors))
        rec = manifest.get("legs", {}).get("openrouter_models", {})
        if rec.get("status") == "ok":
            router.extend(
                _openrouter(
                    day,
                    folder / "openrouter_models.json.gz",
                    iso(rec.get("fetched_at"), errors, day),
                    errors,
                )
            )
        if manifest.get("legs", {}).get("arxiv", {}).get("status") == "ok":
            arxiv.extend(_arxiv(day, folder / "arxiv.jsonl.gz", errors))
        if manifest.get("legs", {}).get("hf_inference_providers", {}).get("status") == "ok":
            models, providers = _hf_providers(
                day, folder / "hf_inference_providers.jsonl.gz", errors
            )
            provider_models.extend(models)
            provider_observations.extend(providers)
    backfill_dir = root / "data" / "backfill" / "arxiv"
    if backfill_dir.exists():
        for backfill_manifest in sorted(backfill_dir.rglob("_manifest.json")):
            info = read_json(backfill_manifest)
            if info.get("leg", {}).get("status") == "ok":
                observed = info["collected_at"][:10]
                arxiv.extend(
                    _arxiv(observed, backfill_manifest.parent / "arxiv.jsonl.gz", errors, root)
                )
    atom_papers, resolutions = _atom_enrichment(root, errors)
    arxiv.extend(atom_papers)
    arxiv_by_day = {}
    for row in arxiv:
        arxiv_by_day.setdefault((row["snapshot_date"], row["arxiv_id"]), row)
    arxiv = [arxiv_by_day[key] for key in sorted(arxiv_by_day)]
    _first_seen(hf, "hf_repo_id", "source_leg")
    _first_seen(router, "openrouter_model_id")
    for row in router:
        row["first_seen_source"] = "openrouter_models"
        row["age_at_first_seen_days"] = age_days(row["created_at"], row["first_seen_date"])
    _first_seen(arxiv, "arxiv_id")
    first_provider_model = {}
    for row in provider_models:
        rid = row["hf_repo_id"]
        first_provider_model[rid] = min(
            row["snapshot_date"], first_provider_model.get(rid, row["snapshot_date"])
        )
    for row in provider_models:
        row["first_observed_provider_catalogue_date"] = first_provider_model[row["hf_repo_id"]]
    first_provider = {}
    for row in provider_observations:
        key = row["hf_repo_id"], row["provider_name"]
        first_provider[key] = min(
            row["snapshot_date"], first_provider.get(key, row["snapshot_date"])
        )
    for row in provider_observations:
        row["first_seen_provider_date"] = first_provider[
            (row["hf_repo_id"], row["provider_name"])
        ]
    # Detail endpoint wins over top list, which wins over newest list; one row per repo/day.
    canonical = {}
    for row in hf:
        key = (row["snapshot_date"], row["hf_repo_id"])
        if (
            key not in canonical
            or PRECEDENCE[row["source_leg"]] < PRECEDENCE[canonical[key]["source_leg"]]
        ):
            canonical[key] = row
    repos = [canonical[key] for key in sorted(canonical)]
    platform = _links(
        router,
        "openrouter_model_id",
        "hugging_face_id",
        "declares_hf_repo",
        "explicit_hugging_face_id",
        "explicit_metadata",
    )
    paper_rows = []
    for row in repos:
        for aid in json.loads(row["arxiv_ids"]):
            paper_rows.append(
                {
                    "hf_repo_id": row["hf_repo_id"],
                    "arxiv_id": aid,
                    "snapshot_date": row["snapshot_date"],
                }
            )
    paper_links = _links(
        paper_rows,
        "hf_repo_id",
        "arxiv_id",
        "references_arxiv_paper",
        "hf_arxiv_tag",
        "explicit_metadata",
    )
    paper_links = [
        {
            "hf_repo_id": x["source_entity"],
            "arxiv_id": x["target_entity"],
            "match_method": x["match_method"],
            "confidence": x["match_confidence"],
            "evidence": "arxiv:" + x["target_entity"],
            "first_seen_date": x["first_seen_date"],
            "last_seen_date": x["last_seen_date"],
        }
        for x in paper_links
    ]
    available_papers = {row["arxiv_id"] for row in arxiv}
    lookup_status = {row["arxiv_id"]: row["resolution_status"] for row in resolutions}
    for link in paper_links:
        aid = link["arxiv_id"]
        link["resolution_status"] = (
            "resolved" if aid in available_papers else lookup_status.get(aid, "not_looked_up")
        )
    first_repo = {}
    for row in repos:
        first_repo.setdefault(row["hf_repo_id"], row)
    linked_hf = {x["target_entity"] for x in platform}
    paper_hf = {x["hf_repo_id"] for x in paper_links}
    cohorts = []
    for repo_id, row in sorted(first_repo.items()):
        day = row["first_seen_date"]
        cohorts.append(
            {
                "hf_repo_id": repo_id,
                "created_at": row["created_at"],
                "first_seen_date": day,
                "cohort_month": day[:7],
                "cohort_week": dt.date.fromisoformat(day).strftime("%G-W%V"),
                "first_seen_source": row["first_seen_source"],
                "age_at_first_seen_days": row["age_at_first_seen_days"],
                "initial_downloads_30d": row["downloads_30d"],
                "initial_likes": row["likes"],
                "base_model": row["base_model"],
                "arxiv_link_present": repo_id in paper_hf,
                "openrouter_link_present": repo_id in linked_hf,
            }
        )
    tables = {
        "hf_observations": hf,
        "hf_repositories": repos,
        "openrouter_models": router,
        "arxiv_papers": arxiv,
        "arxiv_id_resolutions": resolutions,
        "model_platform_links": platform,
        "hf_arxiv_links": paper_links,
        "hf_repo_cohorts": cohorts,
        "hf_provider_model_observations": provider_models,
        "hf_inference_provider_observations": provider_observations,
    }
    for name, rows in tables.items():
        _write(output, name, rows)
    duplicates = Counter((x["snapshot_date"], x["source_leg"], x["hf_repo_id"]) for x in hf)
    router_duplicates = Counter((x["snapshot_date"], x["openrouter_model_id"]) for x in router)
    provider_model_duplicates = Counter(
        (x["snapshot_date"], x["hf_repo_id"]) for x in provider_models
    )
    provider_duplicates = Counter(
        (x["snapshot_date"], x["hf_repo_id"], x["provider_name"])
        for x in provider_observations
    )
    report = {
        "snapshot_days": len(days),
        "coverage": [days[0], days[-1]] if days else [],
        "rows": {name: len(rows) for name, rows in tables.items()},
        "unique_hf_repos": len(first_repo),
        "unique_openrouter_models": len({r["openrouter_model_id"] for r in router}),
        "openrouter_rows_with_hf_id": sum(bool(r["hugging_face_id"]) for r in router),
        "unique_hf_repos_referenced_by_openrouter": len(linked_hf),
        "hf_repos_with_arxiv_tag": len(paper_hf),
        "arxiv_references_resolved": len(
            {x["arxiv_id"] for x in paper_links} & {x["arxiv_id"] for x in arxiv}
        ),
        "arxiv_lookup_status": dict(Counter(row["resolution_status"] for row in resolutions)),
        "hf_repos_with_resolved_paper": len(
            {link["hf_repo_id"] for link in paper_links if link["resolution_status"] == "resolved"}
        ),
        "openrouter_hf_id_rate": (
            sum(bool(r["hugging_face_id"]) for r in router) / len(router) if router else None
        ),
        "hf_repo_arxiv_tag_rate": len(paper_hf) / len(first_repo) if first_repo else None,
        "null_rates": {
            "hf_repositories": {
                key: sum(row.get(key) is None for row in repos) / len(repos)
                for key in (
                    "created_at",
                    "last_modified",
                    "downloads_30d",
                    "likes",
                    "base_model",
                    "library_name",
                )
            },
            "openrouter_models": {
                key: sum(row.get(key) is None for row in router) / len(router)
                for key in ("hugging_face_id", "created_at", "canonical_slug")
            },
            "arxiv_papers": {
                key: sum(row.get(key) is None for row in arxiv) / len(arxiv)
                for key in ("submitted_at", "updated_at", "oai_datestamp", "doi")
            }
            if arxiv
            else {},
        }
        if repos and router
        else {},
        "duplicate_hf_within_leg": sum(n - 1 for n in duplicates.values() if n > 1),
        "duplicate_openrouter_ids": sum(n - 1 for n in router_duplicates.values() if n > 1),
        "duplicate_provider_models": sum(
            n - 1 for n in provider_model_duplicates.values() if n > 1
        ),
        "duplicate_provider_mappings": sum(n - 1 for n in provider_duplicates.values() if n > 1),
        "provider_status_counts": dict(Counter(x["status"] for x in provider_observations)),
        "errors_count": len(errors),
        "errors": errors[:100],
    }
    (output / "dq_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="silver")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = build(args.root, args.output)
    print(json.dumps(report, indent=2))
    return (
        1
        if report["errors_count"]
        or report["duplicate_hf_within_leg"]
        or report["duplicate_openrouter_ids"]
        or report["duplicate_provider_models"]
        or report["duplicate_provider_mappings"]
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
