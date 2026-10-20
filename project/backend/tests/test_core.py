import unittest

from project.backend.app.config import Limits
from project.backend.app.core.budget import Budget, BudgetExceeded
from project.backend.app.core.graph import find_cycle, topo_layers
from project.backend.app.core.pricing import PricingTable
from project.backend.app.core.schemas import PlanValidationError, parse_plan
from project.backend.app.core.workspace import CommandRunner, Workspace, WorkspaceError, parse_file_blocks
from project.backend.app.core.exporters import build_docx, build_pptx, parse_json_loose
from project.backend.app.logging_utils import REDACTOR, redact
from project.backend.tests.helpers import plan_json, task
import json, tempfile
from pathlib import Path


class GraphTests(unittest.TestCase):
    def test_cycle_detected(self):
        self.assertIsNotNone(find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}))
        self.assertIsNone(find_cycle({"a": [], "b": ["a"], "c": ["a", "b"]}))

    def test_layers_parallel_groups(self):
        self.assertEqual(topo_layers({"a": [], "b": [], "c": ["a", "b"], "d": ["c"]}), [["a", "b"], ["c"], ["d"]])

    def test_layers_cycle_raises(self):
        with self.assertRaises(ValueError):
            topo_layers({"a": ["b"], "b": ["a"]})


class PlanValidationTests(unittest.TestCase):
    def parse(self, *tasks, **kw):
        return parse_plan(json.loads(plan_json(*tasks)), **kw)

    def test_valid_plan(self):
        p = self.parse(task("a"), task("b", deps=["a"]))
        self.assertEqual([t.id for t in p.tasks], ["a", "b"])
        self.assertEqual(p.tasks[0].expected_outputs[0]["type"], "markdown")

    def errors(self, *tasks, **kw):
        with self.assertRaises(PlanValidationError) as cm:
            self.parse(*tasks, **kw)
        return " | ".join(cm.exception.errors)

    def test_cycle_rejected(self):
        self.assertIn("cycle", self.errors(task("a", deps=["b"]), task("b", deps=["a"])))

    def test_unknown_dependency_and_self_dependency(self):
        e = self.errors(task("a", deps=["zzz"]), task("b", deps=["b"]))
        self.assertIn("unknown task 'zzz'", e)
        self.assertIn("depends on itself", e)

    def test_duplicate_ids_and_bad_kind(self):
        e = self.errors(task("a"), task("a"), task("c", kind="teleport"))
        self.assertIn("duplicate", e)
        self.assertIn("unknown kind", e)

    def test_task_limit(self):
        self.assertIn("too many tasks", self.errors(*[task(f"t{i}") for i in range(5)], max_tasks=3))

    def test_cannot_promise_video_file_from_script_task(self):
        e = self.errors(task("v", kind="video_script", expected_outputs=[{"name": "movie", "type": "video"}]))
        self.assertIn("cannot produce 'video'", e)

    def test_acceptance_criteria_required(self):
        t = task("a")
        t["acceptance_criteria"] = []
        self.assertIn("acceptance_criteria", self.errors(t))

    def test_non_object(self):
        with self.assertRaises(PlanValidationError):
            parse_plan([1, 2])


class BudgetTests(unittest.TestCase):
    def mk(self, **lim):
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "unpriced_calls": 0, "simulated_calls": 0}
        pricing = PricingTable({"openai": {"m": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}})
        return Budget(Limits(**lim), usage, pricing), usage

    def test_usd_hard_stop(self):
        b, u = self.mk(max_usd=0.001, max_total_tokens=10**9)
        b.reserve("openai", "m", 100, 100)                 # worst case $0.0003
        b.record("openai", "m", 100_000, 100_000)           # actual $0.30 -> over
        with self.assertRaises(BudgetExceeded):
            b.reserve("openai", "m", 1, 1)

    def test_unknown_price_fails_closed_with_usd_limit(self):
        b, _ = self.mk(max_usd=5.0)
        with self.assertRaises(BudgetExceeded):
            b.reserve("openai", "other-model", 10, 10)

    def test_unknown_price_allowed_without_usd_limit_but_counted(self):
        b, u = self.mk()
        b.reserve("openai", "other-model", 10, 10)
        b.record("openai", "other-model", 10, 10)
        self.assertEqual(u["unpriced_calls"], 1)

    def test_token_and_call_caps(self):
        b, u = self.mk(max_total_tokens=1000, max_calls=1)
        with self.assertRaises(BudgetExceeded):
            b.reserve("openai", "m", 900, 200)
        b.record("openai", "m", 1, 1)
        with self.assertRaises(BudgetExceeded):
            b.reserve("openai", "m", 1, 1)

    def test_simulated_calls_bypass(self):
        b, u = self.mk(max_usd=0.0, max_calls=1)
        b.reserve("demo", "x", 10**6, 10**6, simulated=True)
        b.record("demo", "x", 1, 1, simulated=True)
        self.assertEqual(u["usd"], 0.0)

    def test_limits_are_clamped(self):
        l = Limits(max_tasks=9999, max_retries=99, max_duration_s=10**9).clamp()
        self.assertEqual((l.max_tasks, l.max_retries, l.max_duration_s), (30, 5, 3600))


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(Path(tempfile.mkdtemp()) / "w")

    def test_path_traversal_and_forbidden(self):
        for bad in ["../x.js", "/etc/passwd", "a/../../x.js", ".env", "node_modules/x.js", "run.exe", "C:\\x.js", "a\x00.js"]:
            with self.assertRaises(WorkspaceError, msg=bad):
                self.ws.safe_path(bad)

    def test_symlink_escape_blocked(self):
        outside = Path(tempfile.mkdtemp())
        (self.ws.root / "link").symlink_to(outside)
        with self.assertRaises(WorkspaceError):
            self.ws.safe_path("link/x.js")

    def test_apply_tracks_changes(self):
        c1 = self.ws.apply_files([("index.html", "<p>1</p>")])
        c2 = self.ws.apply_files([("index.html", "<p>2</p>"), ("src/a.ts", "x")])
        self.assertEqual(c1[0]["action"], "created")
        self.assertEqual([c["action"] for c in c2], ["modified", "created"])

    def test_batch_is_validated_before_any_write(self):
        with self.assertRaises(WorkspaceError):
            self.ws.apply_files([("ok.js", "1"), ("../bad.js", "2")])
        self.assertEqual(self.ws.files(), {})

    def test_file_block_parser(self):
        t = "=== FILE: a.js ===\nconsole.log(1)\n=== END FILE ===\n=== FILE: b/c.css ===\nbody{}\n=== END FILE ==="
        self.assertEqual(parse_file_blocks(t), [("a.js", "console.log(1)"), ("b/c.css", "body{}")])

    def test_zip_roundtrip(self):
        import io, zipfile
        self.ws.apply_files([("index.html", "hi")])
        self.assertEqual(zipfile.ZipFile(io.BytesIO(self.ws.package_zip())).namelist(), ["index.html"])

    def test_runner_disabled_by_default_and_allowlist(self):
        self.assertIn("disabled", CommandRunner(False).run(self.ws, ["npm", "run", "build"]).skipped_reason)
        r = CommandRunner(True)
        self.assertFalse(r.allowed(["rm", "-rf", "/"]))
        self.assertFalse(r.allowed(["npm", "install", "left-pad"]))
        self.assertFalse(r.allowed(["npm", "run", "evil"]))
        self.assertTrue(r.allowed(["npm", "run", "build"]))
        self.assertIn("allow-list", r.run(self.ws, ["curl", "http://x"]).skipped_reason)


class MiscTests(unittest.TestCase):
    def test_redaction(self):
        REDACTOR.register("my-very-secret-value-123")
        s = redact("key=my-very-secret-value-123 and sk-ant-abcdefgh12345678 and AIzaSyA1234567890abcdefghij and sk-abcdefghijklmnopqrstuv")
        for leak in ("my-very-secret", "sk-ant-abcdefgh", "AIzaSyA1234", "sk-abcdefghijkl"):
            self.assertNotIn(leak, s)
        self.assertNotIn("topsecret", redact("Authorization: Bearer topsecret"))

    def test_json_loose(self):
        self.assertEqual(parse_json_loose('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(parse_json_loose('Sure! {"a": 2} hope that helps'), {"a": 2})
        with self.assertRaises(ValueError):
            parse_json_loose("no json here")

    def test_exporters_produce_real_files(self):
        pptx = build_pptx({"slides": [{"title": "A", "bullets": ["x"]}, {"title": "B", "bullets": ["y", "z"], "notes": "n"}]})
        docx = build_docx("# T\n\n- a\n\ntext")
        self.assertTrue(pptx.startswith(b"PK") and docx.startswith(b"PK"))
        with self.assertRaises(ValueError):
            build_pptx({"slides": []})

    def test_pricing_unknown_vs_known(self):
        p = PricingTable({"openai": {"m": {"input_per_mtok": 1.0, "output_per_mtok": 3.0, "per_image": None}}})
        self.assertAlmostEqual(p.cost("openai", "m", 1_000_000, 1_000_000), 4.0)
        self.assertIsNone(p.cost("openai", "zzz", 1, 1))
        self.assertFalse(p.known("openai", "m", image=True))
        self.assertEqual(p.cost("demo", "x", 10**6, 10**6), 0.0)


if __name__ == "__main__":
    unittest.main()
