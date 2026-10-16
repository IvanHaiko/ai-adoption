"""Frozen black-box acceptance tests; run: python tests/test_acceptance.py."""
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.getcwd())
import analysis


def row(day, downloads=100, likes=10, repo="Org/Model", **extra):
    result = dict(hf_repo_id=repo, snapshot_date=day, fetched_at=None,
                  source_leg="hf_models", downloads_30d=downloads, likes=likes,
                  created_at=None, pipeline_tag="text-generation")
    result.update(extra)
    return result


def dates(offsets, missing=()):
    start = dt.date(2026, 1, 1)
    return [row((start + dt.timedelta(days=i)).isoformat(),
                None if i in missing else 100 + i) for i in offsets]


class PublicAcceptance(unittest.TestCase):
    def test_exact_ids_order_precedence_and_repeat_collapse(self):
        winner = row("2026-01-01", 7, 2)
        rows = [row("2026-01-03", 9),
                row("2026-01-01", 999, source_leg="hf_new_models"),
                row("2026-01-01", 88, source_leg="hf_top_models"),
                row("2026-01-01", 5, repo="org/model"), winner, dict(winner),
                row("2026-01-02", 8),
                row("2026-01-01", 6, repo="Org/Model-v2")]
        result = analysis.canonicalize(rows)
        self.assertEqual(len(result), 5)
        self.assertEqual({r["hf_repo_id"] for r in result},
                         {"Org/Model", "org/model", "Org/Model-v2"})
        main = [r for r in result if r["hf_repo_id"] == "Org/Model"]
        self.assertEqual([r["snapshot_date"] for r in main],
                         ["2026-01-01", "2026-01-02", "2026-01-03"])
        self.assertEqual(main[0]["downloads_30d"], 7)
        self.assertEqual(main[0]["likes"], 2)
        top = analysis.canonicalize([row("2026-01-01", 3, source_leg="hf_new_models"),
                                    row("2026-01-01", 4, source_leg="hf_top_models")])
        self.assertEqual(top[0]["downloads_30d"], 4)

    def test_ambiguous_equal_precedence_rejected(self):
        for leg in ("hf_models", "hf_top_models", "hf_new_models"):
            with self.subTest(leg=leg), self.assertRaises(ValueError):
                analysis.canonicalize([row("2026-01-01", 1, source_leg=leg),
                                       row("2026-01-01", 2, source_leg=leg)])

    def test_features_independent_metric_endpoints_and_date_geometry(self):
        rows = [row("2026-01-09", None, 3), row("2026-01-01", None, 10),
                row("2026-01-05", 150, None), row("2026-01-03", 100, 8)]
        f = analysis.repository_features(rows + [dict(rows[0])])
        expected = dict(hf_repo_id="Org/Model", observation_count=4,
                        first_date="2026-01-01", last_date="2026-01-09",
                        span_days=8, max_gap_days=4, download_observation_count=2,
                        download_first=100, download_last=150,
                        download_absolute_change=50, download_relative_change=0.5,
                        likes_first=10, likes_last=3, likes_absolute_change=-7,
                        eligible=False)
        for key, value in expected.items():
            self.assertEqual(f[key], value, key)
        self.assertAlmostEqual(f["coverage_fraction"], 4 / 9)
        # The contract requires endpoint dates but does not prescribe their names.
        for metric, endpoint, date in (("download", "first", "2026-01-03"),
                                       ("download", "last", "2026-01-05"),
                                       ("likes", "first", "2026-01-01"),
                                       ("likes", "last", "2026-01-09")):
            candidates = [v for k, v in f.items()
                          if metric in k and endpoint in k and "date" in k]
            self.assertIn(date, candidates, (metric, endpoint, f))

    def test_null_singleton_and_zero_baselines(self):
        for values, endpoints, absolute, relative in (
                ([None, None], (None, None), None, None),
                ([None, 12], (12, 12), 0, 0.0),
                ([0, 12], (0, 12), 12, None),
                ([0, 0], (0, 0), 0, None)):
            with self.subTest(values=values):
                f = analysis.repository_features([row("2026-01-01", values[0], None),
                                                  row("2026-01-02", values[1], None)])
                self.assertEqual((f["download_first"], f["download_last"]), endpoints)
                self.assertEqual(f["download_absolute_change"], absolute)
                self.assertEqual(f["download_relative_change"], relative)
                for k in ("likes_first", "likes_last", "likes_absolute_change"):
                    self.assertIsNone(f[k])
        f = analysis.repository_features([row("2026-01-01", None, 0)])
        self.assertEqual(f["span_days"], 0)
        self.assertEqual(f["max_gap_days"], 0)
        self.assertEqual(f["coverage_fraction"], 1)
        self.assertEqual(f["likes_absolute_change"], 0)

    def test_intervals_null_rows_do_not_bridge_and_zero(self):
        rows = [row("2026-01-01", 0, 10), row("2026-01-02", 20, None),
                row("2026-01-03", None, 7), row("2026-01-04", 5, 4)]
        changes = analysis.interval_changes(list(reversed(rows)) + [dict(rows[0])])
        self.assertEqual(len(changes), 3)
        for i, c in enumerate(changes):
            self.assertEqual(c["hf_repo_id"], "Org/Model")
            self.assertEqual(c["from_date"], rows[i]["snapshot_date"])
            self.assertEqual(c["to_date"], rows[i + 1]["snapshot_date"])
            self.assertEqual(c["elapsed_days"], 1)
        self.assertEqual(changes[0]["download_change"], 20)
        self.assertEqual(changes[0]["download_change_per_day"], 20)
        self.assertIsNone(changes[0]["download_relative_change"])
        for c in changes[1:]:
            for k in ("download_change", "download_change_per_day", "download_relative_change"):
                self.assertIsNone(c[k])
        self.assertEqual([c["likes_change"] for c in changes], [None, None, -3])

    def test_actual_utc_intervals_and_rolling_decrease(self):
        # A 30-day window loses 400 old downloads and gains 100 new downloads:
        # its stock falls 1000 -> 700 despite 100 actual downloads in this period.
        rows = [row("2026-01-01", 1000, 50, fetched_at="2026-01-01T23:00:00Z"),
                row("2026-01-03", 700, 48, fetched_at="2026-01-03T13:00:00+02:00")]
        c = analysis.interval_changes(list(reversed(rows)))[0]
        self.assertEqual(c["elapsed_days"], 1.5)
        self.assertEqual(c["download_change"], -300)
        self.assertEqual(c["download_change_per_day"], -200)
        self.assertEqual(c["download_relative_change"], -0.3)
        self.assertEqual(c["likes_change"], -2)

    def test_fallback_is_explicit_and_nonpositive_time_errors(self):
        c = analysis.interval_changes([row("2026-01-01", 100,
                                           fetched_at="2026-01-01T12:00:00Z"),
                                       row("2026-01-04", 160)])[0]
        self.assertEqual(c["elapsed_days"], 3)
        self.assertEqual(c["download_change_per_day"], 20)
        # Accept a boolean fallback flag or a descriptive time-basis field.
        markers = {k: v for k, v in c.items()
                   if any(s in k.lower() for s in ("fallback", "basis", "source", "method"))}
        self.assertTrue(any(("fallback" in k.lower() and v is True) or
                            (isinstance(v, str) and ("date" in v.lower() or "fallback" in v.lower()))
                            for k, v in markers.items()), markers)
        for timestamp in ("2026-01-01T12:00:00Z", "2026-01-01T11:00:00Z"):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                analysis.interval_changes([row("2026-01-01", fetched_at="2026-01-01T12:00:00Z"),
                                           row("2026-01-02", fetched_at=timestamp)])

    def test_eligibility_each_boundary_and_missingness(self):
        # 24/30 = .8 exactly, longest spacing three days; span 29.
        boundary = [i for i in range(30) if i not in (2, 3, 7, 11, 15, 19)]
        cases = [(boundary, (), True),
                 (boundary, (0, 1, 4), True),  # exactly 21 measured downloads
                 (boundary, (0, 1, 4, 5), False),
                 (list(range(28)), (), False),  # span 27
                 (list(range(29)), (), True),   # span 28
                 ([i for i in range(30) if i not in (2, 3, 7, 11, 15, 19, 23)], (), False),
                 ([i for i in range(30) if i not in (2, 3, 4)], (), False)]
        for offsets, missing, expected in cases:
            with self.subTest(offsets=offsets, missing=missing):
                self.assertEqual(analysis.repository_features(dates(offsets, missing))["eligible"], expected)


class CLIArtifacts(unittest.TestCase):
    def test_fixture_artifacts_consistency_and_deterministic_rerun(self):
        scratch = Path.cwd() / "scratch"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="acceptance-", dir=scratch) as temporary:
            root = Path(temporary)
            fixture = root / "fixture.csv"
            rows = dates(range(35)) + dates(range(35))
            for r in rows[35:]:
                r["hf_repo_id"] = "org/model"
                r["downloads_30d"] = 500 - 5 * (dt.date.fromisoformat(r["snapshot_date"]) - dt.date(2026, 1, 1)).days
            rows[4]["downloads_30d"] = None
            with fixture.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(reversed(rows))
            original = fixture.read_bytes()
            names = ("summary.json", "repository_features.csv", "interval_changes.csv",
                     "cases.csv", "case_observations.csv", "REPORT.md", "AUDIT.md")
            outputs = [root / "first", root / "second"]
            for output in outputs:
                run = subprocess.run([sys.executable, "analysis.py", "--input", str(fixture),
                                      "--output", str(output)], capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                for name in names:
                    self.assertTrue((output / name).is_file(), name)
                self.assertEqual(fixture.read_bytes(), original)
            for name in names:
                self.assertEqual((outputs[0] / name).read_bytes(), (outputs[1] / name).read_bytes(), name)
            json.loads((outputs[0] / "summary.json").read_text())
            def table(name):
                return list(csv.DictReader(io.StringIO((outputs[0] / name).read_text())))
            features = table("repository_features.csv")
            self.assertEqual({r["hf_repo_id"] for r in features}, {"Org/Model", "org/model"})
            self.assertEqual(len(features), 2)
            self.assertEqual(sorted(int(r["observation_count"]) for r in features), [35, 35])
            intervals = table("interval_changes.csv")
            self.assertEqual(len(intervals), 68)
            source = {(r["hf_repo_id"], r["snapshot_date"]): r for r in rows}
            for c in intervals:
                a = source[(c["hf_repo_id"], c["from_date"])]
                b = source[(c["hf_repo_id"], c["to_date"])]
                expected = None if None in (a["downloads_30d"], b["downloads_30d"]) else b["downloads_30d"] - a["downloads_30d"]
                if expected is None:
                    self.assertIn(c["download_change"].lower(), ("", "none", "null"))
                else:
                    self.assertEqual(float(c["download_change"]), expected)
            cases = table("cases.csv")
            observations = table("case_observations.csv")
            case_repos = {r["hf_repo_id"] for r in cases}
            self.assertTrue(case_repos <= {"Org/Model", "org/model"})
            self.assertEqual({r["hf_repo_id"] for r in observations}, case_repos)
            for observed in observations:
                key = (observed["hf_repo_id"], observed["snapshot_date"])
                self.assertIn(key, source)
                if "downloads_30d" in observed:
                    value = source[key]["downloads_30d"]
                    if value is None:
                        self.assertIn(observed["downloads_30d"].lower(), ("", "none", "null"))
                    else:
                        self.assertEqual(float(observed["downloads_30d"]), value)
            for name in ("REPORT.md", "AUDIT.md"):
                self.assertTrue((outputs[0] / name).read_text().strip())


if __name__ == "__main__":
    unittest.main()
