import json
import threading
import time
import unittest

from app.core.capabilities import TEXT, JSON, CODE, IMAGE_GEN
from app.core.router import Router
from app.core.pricing import PricingTable
from app.providers.base import CredentialStore, ProviderError
from app.providers.registry import ProviderRegistry
from app.service import Conflict, Invalid, NotFound, ProjectService
from tests.helpers import FakeRunner, Mock, make_service, new_project, plan_json, task


def planner(plan, other=None):
    def h(req):
        if req.metadata.get("task_kind") == "planning":
            return plan
        if other:
            return other(req)
        return f"Output for {req.metadata.get('task_id')}: " + "lorem ipsum " * 5
    return h


def calls_for(mock, tid):
    return [c for c in mock.calls if not isinstance(c, str) and c.metadata.get("task_id") == tid]


def statuses(d):
    return {t["id"]: t["status"] for t in d["plan"]["tasks"]}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.a = Mock("alpha", lambda r: "x")
        self.b = Mock("beta", lambda r: "x", caps={TEXT, JSON, CODE, IMAGE_GEN})
        self.router = Router(ProviderRegistry(CredentialStore(env={}), extra=[self.a, self.b]), PricingTable())

    def test_capability_filtering(self):
        d, _ = self.router.route("image", "integration")
        self.assertEqual(d.provider, "beta")

    def test_no_provider_explains_why(self):
        d, why = self.router.route("video_assets", "integration")
        self.assertIsNone(d)
        self.assertIn("does not support video_generation", why)

    def test_manual_choice_and_preference_order_and_exclusion(self):
        self.assertEqual(self.router.route("writing", "integration", preferred="beta")[0].provider, "beta")
        self.assertEqual(self.router.route("writing", "integration", settings={"provider_order": ["beta", "alpha"]})[0].provider, "beta")
        self.assertEqual(self.router.route("writing", "integration", settings={"provider_preferences": {"writing": "beta"}})[0].provider, "beta")
        d, _ = self.router.route("writing", "integration", preferred="beta", exclude={"beta"})
        self.assertEqual(d.provider, "alpha")
        self.assertIn("not eligible", d.reason)

    def test_modes_are_isolated(self):
        self.assertEqual(self.router.route("writing", "demo")[0].provider, "demo")
        self.assertNotEqual(self.router.route("writing", "integration")[0].provider, "demo")

    def test_cheapest_known_price_breaks_ties(self):
        pr = PricingTable({"alpha": {"alpha-model": {"input_per_mtok": 9, "output_per_mtok": 9}},
                           "beta": {"beta-model": {"input_per_mtok": 1, "output_per_mtok": 1}}})
        r = Router(ProviderRegistry(CredentialStore(env={}), extra=[self.a, self.b]), pr)
        self.assertEqual(r.route("writing", "integration")[0].provider, "beta")


class PlanningTests(unittest.TestCase):
    def test_invalid_plan_gets_one_repair_round(self):
        bad = plan_json(task("a", deps=["b"]), task("b", deps=["a"]))
        good = plan_json(task("a"), task("b", deps=["a"]))
        seq = [bad, good]
        m = Mock("alpha", lambda r: seq.pop(0))
        svc = make_service([m])
        pid = new_project(svc)
        d = svc.generate_plan(pid)
        self.assertEqual(len(m.calls), 2)
        self.assertIn("cycle", m.calls[1].prompt)
        self.assertEqual([t["id"] for t in d["plan"]["tasks"]], ["a", "b"])

    def test_two_invalid_plans_raise_and_nothing_executes(self):
        m = Mock("alpha", lambda r: "not json at all")
        svc = make_service([m])
        pid = new_project(svc)
        with self.assertRaises(Invalid):
            svc.generate_plan(pid)
        self.assertEqual(len(m.calls), 2)                    # bounded: exactly 2 attempts
        self.assertIsNone(svc.get_project(pid)["plan"])

    def test_no_provider_gives_actionable_error(self):
        svc = make_service([])
        pid = new_project(svc)
        with self.assertRaises(Invalid) as cm:
            svc.generate_plan(pid)
        self.assertIn("demo mode", str(cm.exception).lower())

    def test_each_task_gets_its_own_prompt(self):
        m = Mock("alpha", planner(plan_json(task("a"), task("c", kind="code_files", deps=["a"]))))
        svc = make_service([m])
        pid = new_project(svc)
        d = svc.generate_plan(pid)
        pa, pc = (t["prompt_user"] for t in d["plan"]["tasks"])
        self.assertNotEqual(pa, pc)
        self.assertIn("=== FILE:", pc)                       # kind-specific output contract
        for section in ("Objective", "Quality criteria", "Do not change", "Reporting uncertainty"):
            self.assertIn(section, pa)

    def test_planned_cost_is_unknown_without_pricing(self):
        m = Mock("alpha", planner(plan_json(task("a"))))
        svc = make_service([m])
        d = svc.generate_plan(new_project(svc))
        self.assertIsNone(d["plan"]["estimated_cost_usd"])
        self.assertIn("unknown price", d["plan"]["cost_note"])


class ExecutionTests(unittest.TestCase):
    def run_plan(self, plan, handler=None, providers=None, **proj):
        m = Mock("alpha", planner(plan, handler))
        svc = make_service(providers or [m])
        pid = new_project(svc, **proj)
        svc.generate_plan(pid)
        return svc, pid, m, svc.run_sync(pid)

    def test_parallel_execution_and_context_passing(self):
        active, peak, lock = [0], [0], threading.Lock()

        def slow(req):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.25)
            with lock:
                active[0] -= 1
            return f"RESULT-OF-{req.metadata['task_id']} " + "pad " * 10
        t0 = time.time()
        svc, pid, m, d = self.run_plan(plan_json(task("a"), task("b"), task("c", deps=["a", "b"])), slow)
        self.assertEqual(d["status"], "completed")
        self.assertEqual(peak[0], 2, "independent tasks a and b should overlap")
        self.assertLess(time.time() - t0, 0.9)
        prompt_c = calls_for(m, "c")[0].prompt
        self.assertIn("RESULT-OF-a", prompt_c)
        self.assertIn("RESULT-OF-b", prompt_c)
        self.assertNotIn("RESULT-OF-a", calls_for(m, "b")[0].prompt)
        self.assertTrue(all(not a["simulated"] for a in d["artifacts"]))   # real-provider output is never labelled simulated

    def test_transient_error_is_retried_then_succeeds(self):
        n = [0]

        def flaky(req):
            n[0] += 1
            return ProviderError("503", "unavailable", True) if n[0] == 1 else "recovered output " * 3
        svc, pid, m, d = self.run_plan(plan_json(task("a")), flaky)
        t = d["plan"]["tasks"][0]
        self.assertEqual((d["status"], t["status"], len(t["attempts"])), ("completed", "completed", 2))
        self.assertEqual([a["ok"] for a in t["attempts"]], [False, True])
        self.assertEqual(t["attempts"][0]["error_code"], "unavailable")

    def test_retries_are_bounded_and_dependents_blocked(self):
        def always(req):
            return ProviderError("503", "unavailable", True)
        svc, pid, m, d = self.run_plan(plan_json(task("a"), task("b", deps=["a"])), always, limits={"max_retries": 1})
        self.assertEqual(len(calls_for(m, "a")), 2)                    # 1 try + 1 retry, no more
        self.assertEqual(statuses(d), {"a": "failed", "b": "blocked"})
        self.assertEqual(d["plan"]["tasks"][1]["error_code"], "dependency")
        self.assertEqual(d["status"], "failed")

    def test_manual_retry_limit(self):
        mode = {"fail": True}

        def h(req):
            return ProviderError("x", "unavailable", True) if mode["fail"] else "fine output " * 4
        svc, pid, m, d = self.run_plan(plan_json(task("a")), h, limits={"max_retries": 1})
        mode["fail"] = False
        d = svc.retry_task(pid, "a")
        svc._runs[pid][1].join(5)
        self.assertEqual(svc.get_project(pid)["plan"]["tasks"][0]["status"], "completed")
        mode["fail"] = True
        with self.assertRaises(Conflict):
            svc.retry_task(pid, "a")                                   # completed tasks can't be retried

    def test_manual_retry_count_enforced(self):
        svc, pid, m, d = self.run_plan(plan_json(task("a")), lambda r: ProviderError("x", "unavailable", True), limits={"max_retries": 1})
        svc.retry_task(pid, "a"); svc._runs[pid][1].join(5)
        with self.assertRaises(Conflict) as cm:
            svc.retry_task(pid, "a")
        self.assertIn("retry limit", str(cm.exception))

    def test_quota_error_fails_over_to_another_provider(self):
        def alpha(req):
            if req.metadata.get("task_kind") == "planning":
                return plan_json(task("a"))
            return ProviderError("quota exhausted", "quota", False)
        a = Mock("alpha", alpha)
        b = Mock("beta", lambda r: "beta did it " * 5)
        svc = make_service([a, b])
        svc.update_settings({"provider_order": ["alpha", "beta"]})
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        t = d["plan"]["tasks"][0]
        self.assertEqual((t["status"], t["provider"]), ("completed", "beta"))
        self.assertEqual([x["provider"] for x in t["attempts"]], ["alpha", "beta"])
        self.assertEqual(t["attempts"][0]["error_code"], "quota")

    def test_all_providers_failing_marks_failed_with_reason(self):
        svc, pid, m, d = self.run_plan(plan_json(task("a")), lambda r: ProviderError("bad key", "auth", False))
        t = d["plan"]["tasks"][0]
        self.assertEqual(t["status"], "failed")
        self.assertEqual(t["error_code"], "auth")
        self.assertEqual(len(calls_for(m, "a")), 1)                    # auth errors are not hammered

    def test_failed_evaluation_triggers_retry_with_feedback(self):
        verdicts = [{"passed": False, "criteria": [{"criterion": "is good", "met": False, "reason": "too vague"}]},
                    {"passed": True, "criteria": [{"criterion": "is good", "met": True, "reason": "ok"}]}]

        def h(req):
            if req.metadata.get("task_kind") == "evaluation":
                return json.dumps(verdicts.pop(0))
            return "draft text " * 5
        m = Mock("alpha", planner(plan_json(task("a")), h))
        svc = make_service([m], llm_eval=True)
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        t = d["plan"]["tasks"][0]
        self.assertEqual((t["status"], len(t["attempts"])), ("completed", 2))
        self.assertIn("too vague", calls_for(m, "a")[1].prompt)
        self.assertEqual(t["evaluation"]["method"], "structural+llm_judge")

    def test_budget_hard_stop(self):
        svc, pid, m, d = self.run_plan(plan_json(task("a"), task("b", deps=["a"]), task("c", deps=["b"]), task("e", deps=["c"])),
                                       limits={"max_calls": 3})            # 1 plan call + 2 task calls allowed
        self.assertEqual(d["status"], "budget_stopped")
        st = statuses(d)
        self.assertEqual((st["a"], st["b"], st["c"], st["e"]), ("completed", "completed", "failed", "blocked"))
        self.assertEqual(d["plan"]["tasks"][2]["error_code"], "budget")
        self.assertEqual(len(m.calls), 3)                                   # the provider was NOT called a 4th time

    def test_usd_limit_with_unknown_price_blocks_calls(self):
        m = Mock("alpha", planner(plan_json(task("a"))))
        svc = make_service([m])
        pid = new_project(svc, limits={"max_usd": 1.0})
        with self.assertRaises(Invalid) as cm:
            svc.generate_plan(pid)
        self.assertIn("no price is configured", str(cm.exception))
        self.assertEqual(m.calls, [])

    def test_cancellation_blocks_remaining_tasks(self):
        entered, gate = threading.Event(), threading.Event()

        def h(req):
            entered.set()
            gate.wait(5)
            return "done " * 10
        m = Mock("alpha", planner(plan_json(task("a"), task("b", deps=["a"])), h))
        svc = make_service([m])
        pid = new_project(svc)
        svc.generate_plan(pid)
        svc.start_run(pid)
        self.assertTrue(entered.wait(5))
        svc.cancel(pid)
        gate.set()
        svc._runs[pid][1].join(5)
        d = svc.get_project(pid)
        self.assertEqual(d["status"], "cancelled")
        self.assertEqual(d["plan"]["tasks"][1]["error_code"], "cancelled")
        self.assertEqual(len(calls_for(m, "b")), 0)

    def test_user_edited_prompt_is_used(self):
        m = Mock("alpha", planner(plan_json(task("a"))))
        svc = make_service([m])
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.update_task(pid, "a", {"prompt_user": "CUSTOM PROMPT TEXT"})
        self.assertTrue(d["plan"]["tasks"][0]["prompt_edited"])
        svc.run_sync(pid)
        self.assertIn("CUSTOM PROMPT TEXT", calls_for(m, "a")[0].prompt)

    def test_task_edit_validation(self):
        m = Mock("alpha", planner(plan_json(task("a"))))
        svc = make_service([m])
        pid = new_project(svc)
        svc.generate_plan(pid)
        for bad in ({"status": "completed"}, {"prompt_user": "  "}, {"preferred_provider": "nobody"}, {"acceptance_criteria": []}):
            with self.assertRaises(Invalid):
                svc.update_task(pid, "a", bad)
        with self.assertRaises(NotFound):
            svc.update_task(pid, "zzz", {"title": "x"})


class ApprovalAndHandoffTests(unittest.TestCase):
    def test_manual_mode_requires_plan_and_task_approval_in_demo(self):
        svc = make_service([])
        p = svc.create_project({"request": "write an article and a pitch presentation", "mode": "demo", "execution": "manual"})
        svc.generate_plan(p.id)
        with self.assertRaises(Conflict):
            svc.start_run(p.id)                                         # plan not approved
        svc.approve_plan(p.id)
        d = svc.run_sync(p.id)
        self.assertEqual((d["status"], statuses(d)), ("paused", {"copy": "needs_approval", "deck": "waiting"}))
        svc.approve_task(p.id, "copy")
        d = svc.run_sync(p.id)
        self.assertEqual(statuses(d), {"copy": "completed", "deck": "needs_approval"})
        svc.approve_task(p.id, "deck")
        d = svc.run_sync(p.id)
        self.assertEqual(d["status"], "completed")
        with self.assertRaises(Conflict):
            svc.approve_task(p.id, "deck")

    def test_unsupported_task_becomes_manual_handoff_and_resumes(self):
        plan = plan_json(task("a"), task("logo", kind="image", deps=["a"]), task("c", deps=["logo"]))
        m = Mock("alpha", planner(plan))
        svc = make_service([m])
        pid = new_project(svc)
        d = svc.generate_plan(pid)
        self.assertTrue(d["plan"]["tasks"][1]["handoff"])               # visible at plan time
        d = svc.run_sync(pid)
        self.assertEqual(d["status"], "paused")
        self.assertEqual(statuses(d), {"a": "completed", "logo": "blocked", "c": "blocked"})
        self.assertIn("does not support image_generation", d["plan"]["tasks"][1]["error"])
        h = svc.handoff_prompt(pid, "logo")
        self.assertIn("Do logo", h["text"])
        self.assertIn("claude.ai", h["providers"]["anthropic"])
        d = svc.submit_manual_result(pid, "logo", "Logo delivered as https://example.com/logo.png")
        d = svc.run_sync(pid)
        self.assertEqual((d["status"], statuses(d)["c"]), ("completed", "completed"))
        manual = [a for a in d["artifacts"] if a["source"] == "manual"]
        self.assertEqual(len(manual), 1)
        self.assertIn("Logo delivered", calls_for(m, "c")[0].prompt)    # manual output flows downstream
        self.assertIn("[manual]", d["final"]["summary_md"])

    def test_image_generation_requires_approval_and_produces_real_file(self):
        plan = plan_json(task("a"), task("logo", kind="image", deps=["a"]))
        pix = Mock("pix", planner(plan), caps={TEXT, JSON, CODE, IMAGE_GEN})
        svc = make_service([pix])
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        self.assertEqual((d["status"], statuses(d)["logo"]), ("paused", "needs_approval"))
        svc.approve_task(pid, "logo")
        d = svc.run_sync(pid)
        img = [a for a in d["artifacts"] if a["type"] == "image"][0]
        self.assertEqual((img["mime"], img["simulated"], img["provider"]), ("image/png", False, "pix"))
        data, mime, _ = svc.artifact_content(pid, img["id"])
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_video_script_never_claims_video(self):
        plan = plan_json(task("s", kind="video_script"), task("v", kind="video_assets", deps=["s"]))
        svc = make_service([Mock("alpha", planner(plan))])
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        self.assertFalse([a for a in d["artifacts"] if a["type"] == "video"])
        self.assertTrue(any("No video file was produced" in c for c in d["final"]["caveats"]))
        self.assertEqual(statuses(d)["v"], "blocked")


class CodeWorkflowTests(unittest.TestCase):
    CODE_PLAN = plan_json(task("site", kind="code_files"))

    def handler(self):
        def h(req):
            k = req.metadata.get("task_kind")
            if k == "code_repair":
                self.assertIn("Cannot find name 'BUG'", req.prompt)       # compiler errors are sent to the provider
                return "=== FILE: src/main.ts ===\nexport const ok = 1\n=== END FILE ==="
            return ("=== FILE: package.json ===\n{\"scripts\": {\"build\": \"tsc\"}}\n=== END FILE ===\n"
                    "=== FILE: src/main.ts ===\nexport const x = BUG\n=== END FILE ===")
        return h

    def test_build_failure_is_repaired(self):
        m = Mock("alpha", planner(self.CODE_PLAN, self.handler()))
        runner = FakeRunner()
        svc = make_service([m], runner=runner)
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        t = d["plan"]["tasks"][0]
        self.assertEqual(t["status"], "completed")
        v = t["evaluation"]["verification"]
        self.assertEqual((v["status"], v["repairs"]), ("passed", 1))
        self.assertEqual(len(runner.runs), 4)                            # (install, build) x 2 rounds
        self.assertEqual(svc.package_zip(pid)[:2], b"PK")
        self.assertIn("export const ok", svc.preview_file(pid, "src/main.ts")[0].decode())

    def test_repair_attempts_are_bounded(self):
        def h(req):
            return ("=== FILE: package.json ===\n{\"scripts\": {\"build\": \"tsc\"}}\n=== END FILE ===\n"
                    "=== FILE: src/main.ts ===\nBUG\n=== END FILE ===")
        m = Mock("alpha", planner(self.CODE_PLAN, h))
        svc = make_service([m], runner=FakeRunner())
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        t = d["plan"]["tasks"][0]
        self.assertEqual(t["status"], "failed")
        self.assertLessEqual(len(calls_for(m, "site")), 3)               # max_retries(2)+1 generation attempts
        self.assertLess(len(m.calls), 30)

    def test_verification_skipped_honestly_when_execution_disabled(self):
        m = Mock("alpha", planner(self.CODE_PLAN, lambda r: "=== FILE: package.json ===\n{\"scripts\": {\"build\": \"x\"}}\n=== END FILE ==="))
        svc = make_service([m])                                           # default runner: disabled
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        v = d["plan"]["tasks"][0]["evaluation"]["verification"]
        self.assertEqual(v["status"], "skipped")
        self.assertIn("NOT run", v["reason"])

    def test_malicious_paths_rejected_and_fed_back(self):
        outputs = ["=== FILE: ../../etc/evil.js ===\nx\n=== END FILE ===",
                   "=== FILE: index.html ===\n<p>safe</p>\n=== END FILE ==="]
        m = Mock("alpha", planner(self.CODE_PLAN, lambda r: outputs.pop(0)))
        svc = make_service([m])
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        t = d["plan"]["tasks"][0]
        self.assertEqual((t["status"], len(t["attempts"])), ("completed", 2))
        self.assertIn("forbidden path", calls_for(m, "site")[1].prompt)
        self.assertEqual([f["path"] for f in d["workspace_files"]], ["index.html"])

    def test_no_file_blocks_fails_evaluation(self):
        m = Mock("alpha", planner(self.CODE_PLAN, lambda r: "Sure, here is some prose with no files."))
        svc = make_service([m])
        pid = new_project(svc)
        svc.generate_plan(pid)
        d = svc.run_sync(pid)
        self.assertEqual(d["plan"]["tasks"][0]["status"], "failed")


class DemoModeAndProjectTests(unittest.TestCase):
    PROMPT = ("Create a responsive website for a startup, generate a logo, write website content, create a promotional "
              "video workflow and prepare a pitch presentation.")

    def test_full_demo_workflow_without_keys_is_labelled_simulated(self):
        svc = make_service([])
        p = svc.create_project({"request": self.PROMPT, "mode": "demo", "execution": "auto", "demo_options": {"inject_failure": True}})
        d = svc.generate_plan(p.id)
        self.assertTrue(d["plan"]["simulated"])
        self.assertGreaterEqual(len(d["plan"]["tasks"]), 8)
        d = svc.run_sync(p.id)
        st = statuses(d)
        self.assertEqual(d["status"], "paused")                            # only the video-asset handoff is outstanding
        self.assertEqual(st["video_assets"], "blocked")
        self.assertEqual([k for k, v in st.items() if v != "completed"], ["video_assets"])
        self.assertTrue(all(a["simulated"] for a in d["artifacts"] if a["source"] == "ai"))
        self.assertTrue(d["final"]["simulated"])
        self.assertIn("SIMULATED", d["final"]["summary_md"])
        self.assertEqual(d["usage"]["usd"], 0.0)
        self.assertEqual(d["usage"]["input_tokens"], 0)                    # no real tokens consumed
        copy = next(t for t in d["plan"]["tasks"] if t["id"] == "copy")
        self.assertEqual(len(copy["attempts"]), 2, "injected transient failure should have been retried")
        types = {a["type"] for a in d["artifacts"]}
        self.assertTrue({"pptx", "image", "code_bundle", "markdown"} <= types)
        pptx = next(a for a in d["artifacts"] if a["type"] == "pptx")
        self.assertTrue(svc.artifact_content(p.id, pptx["id"])[0].startswith(b"PK"))
        self.assertIn("SIMULATED", svc.preview_file(p.id, "index.html")[0].decode())

    def test_history_survives_restart_and_export_has_no_secrets(self):
        svc = make_service([])
        svc.registry.credentials.set_session_key("openai", "sk-SECRET-should-never-persist-12345")
        p = svc.create_project({"request": "write a blog post about tea", "mode": "demo", "execution": "auto"})
        svc.generate_plan(p.id)
        svc.run_sync(p.id)
        svc2 = ProjectService(svc.config)                                  # fresh process state, same data dir
        self.assertEqual([x["id"] for x in svc2.list_projects()], [p.id])
        d = svc2.get_project(p.id)
        self.assertEqual(d["status"], "completed")
        exported = json.dumps(svc2.export_json(p.id))
        self.assertNotIn("SECRET", exported)
        self.assertTrue(any(a["name"] == "final_summary.md" for a in d["artifacts"]))
        import pathlib
        blob = b"".join(f.read_bytes() for f in pathlib.Path(svc.config.data_dir).rglob("*") if f.is_file())
        self.assertNotIn(b"SECRET-should-never-persist", blob)             # credentials never reach disk

    def test_privacy_setting_omits_prompts_from_export(self):
        svc = make_service([])
        p = svc.create_project({"request": "write a blog post", "mode": "demo", "execution": "auto"})
        svc.generate_plan(p.id)
        svc.update_settings({"privacy": {"include_prompts_in_export": False}})
        t = svc.export_json(p.id)["plan"]["tasks"][0]
        self.assertEqual(t["prompt_user"], "[omitted by privacy setting]")

    def test_uploads_validated_and_included_in_context(self):
        import base64
        m = Mock("alpha", planner(plan_json(task("a"))))
        svc = make_service([m])
        pid = new_project(svc)
        b64 = lambda b: base64.b64encode(b).decode()
        with self.assertRaises(Invalid):
            svc.add_attachment(pid, "evil.exe", b64(b"x"))
        with self.assertRaises(Invalid):
            svc.add_attachment(pid, "a.txt", "!!!not base64")
        with self.assertRaises(Invalid):
            svc.add_attachment(pid, "bin.txt", b64(b"\xff\xfe\x00"))
        with self.assertRaises(Invalid):
            svc.add_attachment(pid, "big.txt", b64(b"x" * 600_000))
        d = svc.add_attachment(pid, "../../notes.txt", b64(b"BRAND COLOR IS TEAL"))
        self.assertEqual(d["attachments"][0]["name"], "notes.txt")           # path components stripped
        svc.generate_plan(pid)
        svc.run_sync(pid)
        self.assertIn("BRAND COLOR IS TEAL", calls_for(m, "a")[0].prompt)
        svc.update_settings({"privacy": {"send_attachments_to_providers": False}})
        m.calls.clear()
        pid2 = new_project(svc)
        svc.add_attachment(pid2, "n.txt", b64(b"SECRET NOTES"))
        svc.generate_plan(pid2)
        svc.run_sync(pid2)
        self.assertNotIn("SECRET NOTES", "".join(c.prompt for c in m.calls if not isinstance(c, str)))

    def test_invalid_ids_and_traversal_are_rejected(self):
        svc = make_service([])
        for bad in ("../etc", "p_../x", "nope"):
            with self.assertRaises(NotFound):
                svc.get_project(bad)
        p = svc.create_project({"request": "write a blog post", "mode": "demo", "execution": "auto"})
        with self.assertRaises(NotFound):
            svc.preview_file(p.id, "../../orchestra.db")
        with self.assertRaises(NotFound):
            svc.artifact_content(p.id, "a_missing")

    def test_settings_validation(self):
        svc = make_service([])
        with self.assertRaises(Invalid):
            svc.update_settings({"unknown": 1})
        with self.assertRaises(Invalid):
            svc.update_settings({"models": {"nobody": {"model": "x"}}})
        s = svc.update_settings({"default_limits": {"max_tasks": 9999}})
        self.assertEqual(s["default_limits"]["max_tasks"], 30)

    def test_provider_statuses_never_leak_keys(self):
        svc = make_service([])
        svc.set_credentials("anthropic", "sk-ant-leaktest-1234567890")
        out = json.dumps(svc.provider_statuses())
        self.assertNotIn("leaktest", out)
        by = {p["id"]: p for p in svc.provider_statuses()}
        self.assertTrue(by["anthropic"]["configured"])
        self.assertEqual(by["anthropic"]["credential_source"], "session")
        self.assertFalse(by["openai"]["configured"])
        self.assertEqual(by["demo"]["connection"], "simulated")
        self.assertNotIn("image_generation", by["anthropic"]["capabilities"])


if __name__ == "__main__":
    unittest.main()
