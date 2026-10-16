"""Read existing Bronze with the Silver parser; write an isolated research extract."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from silver.build import HF_LEGS, PRECEDENCE, _hf

FIELDS = ["hf_repo_id", "snapshot_date", "fetched_at", "source_leg", "downloads_30d",
          "likes", "created_at", "pipeline_tag"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", default="2026-10-13")
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if not output.is_relative_to(root / "research"):
        raise ValueError("Research output must be under the project's research directory")
    output.mkdir(parents=True, exist_ok=True)
    protected = [p for folder in ("raw", "silver", "gold")
                 for p in sorted((root / "data" / folder).rglob("*")) if p.is_file()]
    before = {p.relative_to(root).as_posix(): sha(p) for p in protected}
    rows, errors, days, counts = [], [], [], {}
    for folder in sorted((root / "data/raw").iterdir()):
        if not folder.is_dir() or folder.name > args.cutoff:
            continue
        manifest = folder / "_manifest.json"
        if not manifest.is_file():
            continue
        info = json.loads(manifest.read_text())
        canonical, by_leg = {}, {}
        for leg in HF_LEGS:
            if info.get("legs", {}).get(leg, {}).get("status") != "ok":
                continue
            parsed = _hf(folder.name, leg, folder / (leg + ".jsonl.gz"), errors)
            by_leg[leg] = len(parsed)
            keys = [(r["hf_repo_id"], r["snapshot_date"]) for r in parsed]
            if len(keys) != len(set(keys)):
                errors.append(f"Within-leg duplicate: {folder.name}/{leg}")
            for row in parsed:
                key = row["hf_repo_id"]
                if (key not in canonical
                        or PRECEDENCE[leg] < PRECEDENCE[canonical[key]["source_leg"]]):
                    canonical[key] = row
        days.append(folder.name)
        counts[folder.name] = {"manifest_status": info.get("status"), "legs": by_leg,
                               "canonical_rows": len(canonical)}
        rows.extend(canonical[key] for key in sorted(canonical))
    if errors:
        raise ValueError(errors)
    with (output / "observations.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    after = {p.relative_to(root).as_posix(): sha(p) for p in protected}
    if after != before:
        raise RuntimeError("Protected source data changed during extraction")
    report = {"cutoff": args.cutoff, "snapshot_days": days, "per_day": counts,
              "rows": len(rows), "repositories": len({r["hf_repo_id"] for r in rows}),
              "source_leg_counts": dict(Counter(r["source_leg"] for r in rows)),
              "parser_errors": errors, "source_hashes": before,
              "protected_files_unchanged": after == before,
              "input_sha256": sha(output / "observations.csv"),
              "parser_sha256": sha(root / "silver/build.py"),
              "existing_silver_dq": json.loads((root / "data/silver/dq_report.json").read_text()),
              "existing_gold_summary": json.loads((root / "data/gold/summary.json").read_text())}
    (output / "input_manifest.json").write_text(json.dumps(report, indent=2) + "\n", newline="\n")
    print(json.dumps({k: report[k] for k in ("rows", "repositories", "source_leg_counts",
                                            "protected_files_unchanged", "input_sha256")}))


if __name__ == "__main__":
    main()
