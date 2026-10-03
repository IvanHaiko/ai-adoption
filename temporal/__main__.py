"""Read-only archive replay, baseline protection and identity history CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive import file_hash, fingerprint, output_path, verify, write_new_json
from .baselines import ORIGINAL_BASELINE, activate, selected_baseline
from .deployments import build_deployment_layer
from .identity import build_identity_history, identity_at, validate_observations
from .index import build_index
from .portability import build_proof, proof_path

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
    verify_parser.add_argument("--strict-bytes", action="store_true")
    portability_parser = sub.add_parser("attest-portability", help="attest frozen manifest LF/CRLF")
    portability_parser.add_argument("--baseline", type=Path)
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
    identity_parser = sub.add_parser("identity", help="replay conservative identity history")
    identity_parser.add_argument("--baseline", type=Path)
    identity_parser.add_argument("--observations", type=Path, required=True)
    identity_parser.add_argument("--evidence", type=Path)
    identity_parser.add_argument("--output", type=Path,
                                 default=Path("data/temporal_v2/identity_history.json"))
    identity_parser.add_argument("--dry-run", action="store_true")
    query_parser = sub.add_parser("identity-at", help="query observed identity belief in a replay")
    query_parser.add_argument("--history", type=Path, required=True)
    query_parser.add_argument("--deployment-key", required=True)
    query_parser.add_argument("--at", required=True)
    query_parser.add_argument("--recorded-as-of")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "identity-at":
            history = json.loads(output_path(root, args.history).read_text(encoding="utf-8"))
            print(json.dumps(identity_at(history, args.deployment_key, args.at,
                                         args.recorded_as_of), indent=2))
            return 0
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
        if args.command == "attest-portability":
            proof = build_proof(root, baseline)
            relative_proof = proof_path(root, baseline).relative_to(root).as_posix()
            print(json.dumps({"manifest_count": len(proof["manifests"]),
                              "proof_path": relative_proof}))
            return 0
        before = verify(root, baseline,
                        portable=not (args.command == "verify" and args.strict_bytes))
        if args.command == "verify" or not before["ok"]:
            verification = {**before, "baseline_path": baseline_path.relative_to(root).as_posix()}
            print(json.dumps(verification, indent=2))
            return 0 if before["ok"] else 1
        # Validate destination even on dry-run; never permit raw/v1 outputs.
        output_path(root, args.output)
        archive_before = fingerprint(root)["files"]
        input_hashes = {}
        if args.command == "identity":
            inputs = [output_path(root, args.observations)]
            if args.evidence:
                inputs.append(output_path(root, args.evidence))
            input_hashes = {path: file_hash(path) for path in inputs}
            layer = json.loads(inputs[0].read_text(encoding="utf-8"))
            ledger = json.loads(inputs[1].read_text(encoding="utf-8")) if args.evidence else None
            validate_observations(root, layer, baseline)
            result = build_identity_history(layer, ledger)
            result["input_files"] = {path.relative_to(root).as_posix(): digest
                                     for path, digest in input_hashes.items()}
        elif args.command == "observations":
            index_path = output_path(root, args.index)
            index = json.loads(index_path.read_text(encoding="utf-8"))
            result = build_deployment_layer(root, index)
        else:
            result = build_index(root)
        after = verify(root, baseline)
        if not after["ok"] or before != after or archive_before != fingerprint(root)["files"]:
            raise ValueError("archive changed while indexing; output was not published")
        if any(file_hash(path) != digest for path, digest in input_hashes.items()):
            raise ValueError("identity inputs changed during replay; output was not published")
        if not args.dry_run:
            write_new_json(root, args.output, result)
        summary = result["summary"] if args.command in ("observations", "identity") else {
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
