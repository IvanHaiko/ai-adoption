"""Archive safety CLI: python -m temporal {baseline,verify,index}."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive import fingerprint, output_path, verify, write_new_json
from .baselines import ORIGINAL_BASELINE, activate, selected_baseline
from .deployments import build_deployment_layer
from .index import build_index

BASELINE = ORIGINAL_BASELINE


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="temporal")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    sub = parser.add_subparsers(dest="command", required=True)
    baseline_parser = sub.add_parser("baseline", help="freeze a new stored-byte baseline")
    baseline_parser.add_argument("--output", type=Path, default=BASELINE)
    active_parser = sub.add_parser("activate-baseline", help="append a dated baseline activation")
    active_parser.add_argument("--baseline", type=Path, required=True)
    active_parser.add_argument("--reason", required=True)
    active_parser.add_argument("--accept-enrichment-manifest-change", action="store_true")
    verify_parser = sub.add_parser("verify", help="check every frozen file")
    verify_parser.add_argument("--baseline", type=Path)
    index_parser = sub.add_parser("index", help="read archive metadata into a separate index")
    index_parser.add_argument("--baseline", type=Path)
    index_parser.add_argument("--output", type=Path,
                              default=Path("data/temporal_v2/raw_snapshot_index.json"))
    index_parser.add_argument("--dry-run", action="store_true")
    observations_parser = sub.add_parser("observations", help="replay provider deployment facts")
    observations_parser.add_argument("--baseline", type=Path)
    observations_parser.add_argument("--index", type=Path, required=True)
    observations_parser.add_argument("--output", type=Path,
                                     default=Path("data/temporal_v2/deployment_layer.json"))
    observations_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "baseline":
            result = fingerprint(root)
            write_new_json(root, args.output, result)
            print(json.dumps({k: v for k, v in result.items() if k != "files"}, indent=2))
            return 0
        if args.command == "activate-baseline":
            result = activate(root, args.baseline, args.reason,
                              args.accept_enrichment_manifest_change)
            print(json.dumps({k: v for k, v in result.items()
                              if k != "enrichment_manifest_evidence"}, indent=2))
            return 0
        baseline_path = selected_baseline(root, args.baseline,
                                          require_activated=args.command != "verify")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        before = verify(root, baseline)
        if args.command == "verify" or not before["ok"]:
            verification = {**before, "baseline_path": baseline_path.relative_to(root).as_posix()}
            print(json.dumps(verification, indent=2))
            return 0 if before["ok"] else 1
        # Validate destination even on dry-run; never permit raw/v1 outputs.
        output_path(root, args.output)
        archive_before = fingerprint(root)["files"]
        if args.command == "observations":
            index_path = output_path(root, args.index)
            index = json.loads(index_path.read_text(encoding="utf-8"))
            result = build_deployment_layer(root, index)
        else:
            result = build_index(root)
        after = verify(root, baseline)
        if not after["ok"] or before != after or archive_before != fingerprint(root)["files"]:
            raise ValueError("archive changed while indexing; output was not published")
        if not args.dry_run:
            write_new_json(root, args.output, result)
        summary = result["summary"] if args.command == "observations" else {
            "snapshot_count": result["snapshot_count"]
        }
        print(json.dumps({**summary, "dry_run": args.dry_run, "preservation": after,
                          "baseline_path": baseline_path.relative_to(root).as_posix()}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
