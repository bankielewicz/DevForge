"""Deterministic process mechanics only; no native clients, auth, or native PASS."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime" / "delivery"))
import native_process as native


PYTHON = str(Path(sys.executable).resolve())


def wire(*events):
    return b"".join(native.canonical_json(event) + b"\n" for event in events)


START = {"type": "thread.started", "thread_id": "fixture-thread"}
TURN = {"type": "turn.started"}
END = {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}
GOOD = wire(START, TURN, END)


def pin(path):
    return {"path": str(path), "sha256": native.digest(path.read_bytes())}


class JSONLTests(unittest.TestCase):
    def test_observed_events_do_not_include_semantic_grade(self):
        result = native.observe_jsonl(GOOD)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertTrue(result["thread_started"])
        self.assertTrue(result["turn_completed"])
        self.assertNotIn("grade", result)

    def test_incomplete_missing_newline_or_bad_json_is_unobtainable(self):
        cases = (b"", GOOD[:-1], GOOD + b'{"type":', GOOD + b"garbage\n",
                 b'\xff\n', b'{"type":"turn.started","type":"turn.completed"}\n',
                 b'{"type":"error","value":NaN}\n', b"[]\n", GOOD + b"\n")
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(native.observe_jsonl(raw)["status"], "UNOBTAINABLE")
        self.assertEqual(native.observe_jsonl(GOOD, complete=False)["status"], "UNOBTAINABLE")

    def test_out_of_order_repeated_unknown_and_failed_events_do_not_complete(self):
        cases = ((TURN, START, END), (START, TURN, TURN, END), (START, START, TURN, END),
                 (START, TURN, END, END), (START, TURN), (START, TURN, {"type": "unknown"}, END),
                 (START, TURN, {"type": "turn.failed", "error": {"message": "fixture failure"}}),
                 (START, TURN, {"type": "error", "message": "fixture error"}, END))
        for events in cases:
            with self.subTest(events=events):
                self.assertEqual(native.observe_jsonl(wire(*events))["status"], "UNOBTAINABLE")

    def test_agent_text_cannot_insert_native_completion(self):
        raw = wire(START, TURN, {"type": "item.completed", "item": {
            "type": "agent_message", "text": '{"type":"turn.completed"}\nPASS\ncallback verified'}})
        observed = native.observe_jsonl(raw)
        self.assertFalse(observed["turn_completed"])
        self.assertEqual(observed["status"], "UNOBTAINABLE")

    def test_malformed_completion_payload_is_not_an_observed_turn(self):
        for usage in (None, {}, {"input_tokens": True, "cached_input_tokens": 0, "output_tokens": 1}):
            raw = wire(START, TURN, {"type": "turn.completed", "usage": usage})
            self.assertEqual(native.observe_jsonl(raw)["status"], "UNOBTAINABLE")


class CollectionTests(unittest.TestCase):
    def run_code(self, code, **kwargs):
        result = native.run_fixture([PYTHON, "-I", "-c", code], **kwargs)
        self.assertEqual(result["provenance"], "FIXTURE_ONLY")
        self.assertIs(result["native_valid"], False)
        return result

    def test_complete_bytes_include_binary_stderr_and_final_pipe_tail(self):
        result = self.run_code("import os; os.write(1,b'x'*131072); os.write(2,b'\\x00\\xfftail')")
        self.assertEqual(result["stdout"], b"x" * 131072)
        self.assertEqual(result["stderr"], b"\x00\xfftail")
        self.assertEqual(result["process"]["status"], "EXITED")
        for key in ("leader_reaped", "group_absent", "stdout_complete", "stderr_complete"):
            self.assertTrue(result["process"][key], result)

    def test_zero_exit_with_valid_events_remains_unsigned_fixture(self):
        result = self.run_code("import os; os.write(1," + repr(GOOD) + ")")
        self.assertEqual(result["events"]["status"], "OBSERVED")
        self.assertNotIn("hmac_sha256", result)
        self.assertNotIn("receipt", result)

    def test_nonzero_exit_and_launch_failure_are_not_native_success(self):
        result = self.run_code("import sys; sys.stderr.write('actual failure'); sys.exit(7)")
        self.assertEqual(result["process"]["exit_code"], 7)
        self.assertEqual(result["process"]["status"], "COULD_NOT_RUN")
        absent = native.run_fixture(["/definitely/absent/native-fixture"])
        self.assertEqual(absent["process"]["status"], "LAUNCH_FAILED")
        self.assertTrue(absent["process"]["leader_reaped"])

    def test_deadline_terminates_and_reaps_owned_process(self):
        started = time.monotonic()
        result = self.run_code("import time; print('before timeout', flush=True); time.sleep(20)", timeout=0.15)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result["stdout"], b"before timeout\n")
        self.assertEqual(result["process"]["status"], "TIMED_OUT")
        self.assertTrue(result["process"]["leader_reaped"])
        self.assertTrue(result["process"]["group_absent"])

    def test_output_overflow_has_exact_prefix_and_never_complete_events(self):
        result = self.run_code("import os; os.write(1,b'x'*1000000)", output_limit=1024)
        self.assertEqual(result["stdout"], b"x" * 1024)
        self.assertTrue(result["process"]["output_limit_exceeded"])
        self.assertEqual(result["process"]["status"], "OUTPUT_LIMIT")
        self.assertEqual(result["events"]["status"], "UNOBTAINABLE")

    def test_large_stdin_is_delivered_without_blocking_output_capture(self):
        raw = b"p" * 1048576
        code = "import sys,hashlib; data=sys.stdin.buffer.read(); print(hashlib.sha256(data).hexdigest())"
        result = self.run_code(code, prompt=raw, timeout=3)
        self.assertEqual(result["stdout"], hashlib.sha256(raw).hexdigest().encode() + b"\n")
        self.assertEqual(result["process"]["status"], "EXITED")

    def test_remaining_child_held_pipe_is_a_failure_and_is_stopped(self):
        result = self.run_code("import os,time; child=os.fork(); time.sleep(20) if child==0 else os._exit(0)", timeout=2)
        self.assertEqual(result["process"]["status"], "COULD_NOT_RUN")
        self.assertTrue(result["process"]["leader_reaped"])
        self.assertIn("retained output", " ".join(result["process"]["issues"]))

    def test_cancellation_is_scoped_and_previous_signal_handler_is_restored(self):
        before = signal.getsignal(signal.SIGTERM)
        result = self.run_code("import os,signal,time; os.kill(os.getppid(),signal.SIGTERM); time.sleep(20)")
        self.assertEqual(result["process"]["status"], "CANCELLED")
        self.assertTrue(result["process"]["leader_reaped"])
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_already_expired_original_deadline_never_spawns(self):
        import io
        with mock.patch.object(native.subprocess, "Popen") as spawn:
            result = native._collect(["/must/not/start"], b"", 20, lambda: 20, 100, io.BytesIO(), io.BytesIO())
        spawn.assert_not_called()
        self.assertEqual(result["status"], "TIMED_OUT")

    def test_nonfinite_or_excessive_limits_are_rejected_before_spawn(self):
        for timeout in (float("inf"), float("nan"), -1, 0, 11, True):
            with self.subTest(timeout=timeout), self.assertRaises(native.NativeProcessError):
                native.run_fixture(["/must/not/start"], timeout=timeout)


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.profile = self.root / "profile"
        self.workspace.mkdir()
        self.profile.mkdir()

    def request(self):
        return {"workspace": str(self.workspace), "profile": str(self.profile), "system_mounts": ["/usr/bin", "/usr/lib"],
                "readonly_inputs": [], "fixture_git_dirs": [], "command": ["/selected/client", "exec"]}

    def test_sandbox_starts_empty_and_masks_worktree_git_pointer_without_following_it(self):
        original = "gitdir: /private/framework/.git/worktrees/do-not-open\n"
        (self.workspace / ".git").write_text(original)
        command = native.sandbox_command(self.request())
        self.assertIn("--unshare-pid", command)
        self.assertEqual(command[command.index("--tmpfs") + 1], "/")
        self.assertNotIn("/private/framework/.git/worktrees/do-not-open", command)
        self.assertNotIn(["--ro-bind", "/", "/"], [command[i:i + 3] for i in range(len(command) - 2)])
        self.assertIn(["--ro-bind", "/dev/null", str(self.workspace / ".git")],
                      [command[i:i + 3] for i in range(len(command) - 2)])
        self.assertEqual((self.workspace / ".git").read_text(), original)

    def test_host_home_or_broad_root_system_mount_is_rejected(self):
        for forbidden in ("/", "/home", str(self.root), "/tmp", "/etc"):
            request = self.request()
            request["system_mounts"].append(forbidden)
            with self.subTest(forbidden=forbidden), self.assertRaises(native.NativeProcessError):
                native.sandbox_command(request)

    def test_no_environment_credentials_are_inherited(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-secret", "AWS_SECRET_ACCESS_KEY": "fixture-secret"}):
            command = native.sandbox_command(self.request())
        self.assertNotIn("fixture-secret", command)
        self.assertIn("--clearenv", command)
        self.assertNotIn("--unshare-net", command)  # CLI inference transport remains outside the tool sandbox.

    @unittest.skipUnless(Path("/usr/bin/bwrap").exists(), "Mechanical Bubblewrap fixture requires /usr/bin/bwrap")
    def test_actual_empty_namespace_excludes_unmounted_fixture_and_owns_pid_scope(self):
        excluded = self.root / "excluded-fixture"
        excluded.write_text("Synthetic outside marker, not a credential")
        request = self.request()
        request["system_mounts"] += ["/lib", "/lib64"]
        code = "import os; print(os.path.exists(" + repr(str(excluded)) + ")); print(os.getpid())"
        request["command"] = ["/usr/bin/python3", "-I", "-c", code]
        result = native.run_fixture(native.sandbox_command(request), timeout=3)
        self.assertEqual(result["process"]["status"], "EXITED", result["stderr"])
        self.assertEqual(result["stdout"], b"False\n2\n")
        self.assertFalse(result["native_valid"])

    def test_symlink_and_hardlinked_pins_are_rejected(self):
        file = self.root / "input"
        file.write_text("fixed")
        ref = pin(file)
        link = self.root / "link"
        link.symlink_to(file)
        with self.assertRaises(native.NativeProcessError):
            native._pin({**ref, "path": str(link)})
        link.unlink()
        os.link(file, link)
        with self.assertRaises(native.NativeProcessError):
            native._pin(ref)

    def test_source_change_fails_postrun_freshness(self):
        file = self.root / "input"
        file.write_text("fixed")
        request = {"pins": [pin(file)], "fixture_git_dirs": []}
        self.assertEqual(native._fresh(request)["status"], "INTACT")
        file.write_text("changed")
        with self.assertRaises(native.NativeProcessError):
            native._fresh(request)

    def test_fixture_git_manifest_is_explicit_and_rejects_links(self):
        fixture = self.workspace / "case" / ".git"
        fixture.mkdir(parents=True)
        (fixture / "HEAD").write_text("ref: refs/heads/main\n")
        original = native.tree_digest(fixture)
        (fixture / "objects").mkdir()
        self.assertNotEqual(original, native.tree_digest(fixture))
        (fixture / "escape").symlink_to(self.root)
        with self.assertRaises(native.NativeProcessError):
            native.tree_digest(fixture)

    def test_fixture_cannot_be_promoted_by_signing_with_another_key(self):
        authority, key = native._authority(self.root / "authority", create=True)
        binding = {"fixture": "not a reservation"}
        directory = authority / native.digest(native.canonical_json(binding))
        directory.mkdir(mode=0o700)
        body = {"schema_version": "devforge.native-process-receipt/v1", "provenance": "FIXTURE_ONLY", "binding": binding}
        path = directory / "receipt.json"
        for signing_key in (b"x" * 32, key):
            path.write_bytes(native.canonical_json({"body": body, "hmac_sha256": hmac.new(
                signing_key, native.canonical_json(body), hashlib.sha256).hexdigest()}))
            with self.subTest(key_matches=signing_key == key), self.assertRaises(native.NativeProcessError):
                native.verify_receipt(authority, path, binding)

    def test_receipt_path_and_secret_permissions_are_strict(self):
        authority, _ = native._authority(self.root / "authority", create=True)
        path = self.root / "fake-receipt.json"
        path.write_text("{}")
        with self.assertRaises(native.NativeProcessError):
            native.verify_receipt(authority, path, {})
        (authority / "collector.key").chmod(0o644)
        with self.assertRaises(native.NativeProcessError):
            native._authority(authority)


class ConfigurationTests(unittest.TestCase):
    def config(self):
        return {"model": native.MODEL, "model_reasoning_effort": "medium", "approval_policy": "never",
                "default_permissions": "native", "web_search": "disabled", "forced_login_method": "chatgpt",
                "check_for_update_on_startup": False,
                "permissions": {"native": {"filesystem": {"/": "read", "/prepared": "write", "/private": "deny", "/proc": "deny"},
                                             "network": {"enabled": False}}},
                "model_providers": {"openai": {"request_max_retries": 0, "stream_max_retries": 0}},
                "shell_environment_policy": {"inherit": "none", "set": {"PATH": "/usr/bin:/bin", "HOME": "/prepared", "LANG": "C.UTF-8"}},
                "features": {name: False for name in native.DISABLED_FEATURES}}

    def raw(self, cfg):
        return "\n".join(key + "=" + native._toml(value) for key, value in cfg.items()).encode()

    def test_full_supported_config_passes_structural_selection_only(self):
        cfg = self.config()
        self.assertEqual(native._config(self.raw(cfg), Path("/prepared"), Path("/private")), cfg)

    def test_auth_profile_proc_network_and_nested_model_bypasses_rejected(self):
        mutations = [lambda cfg: cfg["permissions"]["native"]["filesystem"].pop("/private"),
                     lambda cfg: cfg["permissions"]["native"]["filesystem"].pop("/proc"),
                     lambda cfg: cfg["permissions"]["native"]["network"].update(enabled=True),
                     lambda cfg: cfg["features"].update(multi_agent=True),
                     lambda cfg: cfg["features"].update(guardian_approval=True),
                     lambda cfg: cfg.update(approval_policy="on-request"),
                     lambda cfg: cfg.update(mcp_servers={"extra": {"url": "https://invalid"}}),
                     lambda cfg: cfg["model_providers"]["openai"].update(request_max_retries=1),
                     lambda cfg: cfg.update(openai_base_url="https://invalid"),
                     lambda cfg: cfg["shell_environment_policy"].update(inherit="all")]
        for mutation in mutations:
            cfg = self.config()
            mutation(cfg)
            with self.subTest(cfg=cfg), self.assertRaises(native.NativeProcessError):
                native._config(self.raw(cfg), Path("/prepared"), Path("/private"))


class RequestPreparationTests(unittest.TestCase):
    """Schema fixtures exercise preparation only; no fixture enters native launch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace, self.profile = self.root / "workspace", self.root / "profile"
        self.workspace.mkdir()
        self.profile.mkdir()
        client = self.root / "inert-client"
        client.write_text("Never executed schema fixture\n")
        client.chmod(0o700)
        patch = mock.patch.object(native, "CLIENT", {**pin(client), "version": "0.153.4"})
        patch.start()
        self.addCleanup(patch.stop)
        resource = self.workspace / "SKILL.md"
        resource.write_text("Inert fixture instructions")
        self.installed = [pin(resource)]
        cfg = ConfigurationTests().config()
        cfg["permissions"]["native"]["filesystem"] = {
            "/": "read", str(self.workspace): "write", str(self.profile): "deny", "/proc": "deny"}
        cfg["shell_environment_policy"]["set"]["HOME"] = str(self.workspace)
        cfgpath = self.root / "selected.toml"
        cfgpath.write_bytes(ConfigurationTests().raw(cfg))
        prompt = self.root / "prompt.txt"
        prompt.write_text("Do the one allocated fixture task")
        self.plan = {"task_id": "fixture-plan", "client": native.CLIENT.copy(), "model": native.MODEL,
                     "attempts": [{"attempt_id": "C-01", "workspace": str(self.workspace), "client_state": str(self.profile)}]}
        for name in ("candidate", "baseline", "specification", "cases", "boundary_evidence", "arrangement", "allocation", "observation"):
            path = self.root / (name + ".json")
            path.write_bytes(native.canonical_json({"provenance": "FIXTURE_ONLY", "kind": name}))
            self.plan[name] = pin(path)
        self.plan["authentication"] = {"arrangement_ref": self.plan.pop("arrangement")}
        self.report = {"schema_version": "devforge.native-effective-runtime/v1", "attempt_id": "C-01",
                       "client": native.CLIENT.copy(), "model": native.MODEL,
                       "config_sha256": pin(cfgpath)["sha256"], "prompt_sha256": pin(prompt)["sha256"],
                       "workspace": str(self.workspace), "client_state": str(self.profile),
                       "observations": {name: {"status": "OBSERVED", "evidence": self.plan["observation"]}
                                        for name in native.OBSERVATIONS}}
        self.report_path = self.root / "effective.json"
        self.report_path.write_bytes(native.canonical_json(self.report))
        self.runtime = {"schema_version": "devforge.native-runtime-configuration/v1", "allocation": self.plan["allocation"],
                        "attempts": [{"attempt_id": "C-01", "config": pin(cfgpath), "prompt": pin(prompt),
                                      "readonly_inputs": [pin(client), *self.installed], "system_mounts": ["/usr/bin"],
                                      "fixture_git_dirs": [], "effective_runtime": pin(self.report_path),
                                      "managed_worker": None, "interaction": "single-turn"}]}
        discovery_dirs, discovery_files = native.discovery_surfaces(self.workspace, self.profile)
        for directory in discovery_dirs:
            directory.mkdir()
        for path in discovery_files:
            path.write_bytes(b"{}" if path.suffix == ".json" else b"")
        self.runtime["attempts"][0]["readonly_inputs"] += [pin(path) for path in sorted(discovery_files)]
        self.runtime["attempts"][0]["immutable_discovery_dirs"] = [
            {"path": str(path), "sha256": native.tree_digest(path)} for path in sorted(discovery_dirs)]
        self.runtime_path = self.root / "runtime.json"
        self.save()
        self.binding = {"task_id": "fixture-plan", "attempt_id": "C-01", "plan_sha256": "a" * 64,
                        "reservation_sha256": "b" * 64, "schedule_binding": "c" * 64, "challenge": "fixture-nonce"}

    def save(self):
        self.report["runtime_inputs_sha256"] = native.runtime_inputs_digest(self.runtime["attempts"][0])
        self.report_path.write_bytes(native.canonical_json(self.report))
        self.runtime["attempts"][0]["effective_runtime"] = pin(self.report_path)
        self.runtime_path.write_bytes(native.canonical_json(self.runtime))
        self.plan["runtime_configuration"] = pin(self.runtime_path)

    def prepare(self):
        return native.prepare_request(self.plan, self.plan["attempts"][0], self.binding, 100, 700,
                                      "2026-09-07T00:00:00+00:00", self.installed)

    def test_v2_or_unknown_plan_cannot_enter_legacy_collector(self):
        for version in ('devforge.utility-native-plan/v2', 'devforge.utility-native-plan/v999'):
            with self.subTest(version=version):
                self.plan['schema_version'] = version
                with self.assertRaises(native.NativeProcessError):
                    self.prepare()

    def test_interactive_policy_units_must_match_exact_allocation_before_prepare(self):
        policy = {"schema_version": "devforge.native-answer-policy/v1", "steps": [
            {"unit_id": "next", "action": "turn", "text": "Continue"}]}
        path = self.root / "answer-policy.json"
        path.write_bytes(native.canonical_json(policy))
        self.runtime["attempts"][0].update(interaction="awaiting-user", answer_policy=pin(path))
        for units in (["wrong"], [], ["next", "extra"], ["next", "next"], ["next"]):
            allocation_path = Path(self.runtime["allocation"]["path"])
            allocation_path.write_bytes(native.canonical_json({"required_calls": [{"attempt_id": "C-01", "continuation_units": units}]}))
            self.runtime["allocation"] = pin(allocation_path)
            self.save()
            with self.subTest(units=units), mock.patch.object(native.subprocess, "Popen") as spawn:
                if units == ["next"]:
                    request = self.prepare()
                    self.assertEqual(request["answer_policy"], policy)
                else:
                    with self.assertRaisesRegex(native.NativeProcessError, "exact frozen counted allocation"):
                        self.prepare()
                spawn.assert_not_called()
        policy["steps"].append(policy["steps"][0])
        path.write_bytes(native.canonical_json(policy))
        self.runtime["attempts"][0]["answer_policy"] = pin(path)
        self.save()
        with self.assertRaisesRegex(native.NativeProcessError, "duplicate continuation"):
            self.prepare()

    def test_preparation_binds_command_all_inputs_and_original_deadline_without_spawning(self):
        with mock.patch.object(native.subprocess, "Popen") as spawn:
            request = self.prepare()
        spawn.assert_not_called()
        self.assertEqual(request["binding"]["command_sha256"], native.digest(native.canonical_json(request["command"])))
        self.assertEqual(request["binding"]["reserved_at"], 100)
        self.assertEqual(request["binding"]["deadline"], 700)
        self.assertIn("--skip-git-repo-check", request["command"])
        self.assertIn(self.plan["candidate"], request["pins"])
        self.assertNotIn("native_valid", request)

    def test_unobserved_effective_boundary_and_unsupported_interaction_refuse_preparation(self):
        self.report["observations"]["effective_hooks"]["status"] = "NOT_OBSERVED"
        self.save()
        with self.assertRaisesRegex(native.NativeProcessError, "not observed"):
            self.prepare()
        self.report["observations"]["effective_hooks"]["status"] = "OBSERVED"
        self.runtime["attempts"][0]["interaction"] = "operator-answer-schedule"
        self.save()
        with self.assertRaisesRegex(native.NativeProcessError, "Interactive answer transport"):
            self.prepare()

    def test_omitted_installed_input_and_source_drift_refuse_preparation(self):
        self.runtime["attempts"][0]["readonly_inputs"].remove(self.installed[0])
        self.save()
        with self.assertRaisesRegex(native.NativeProcessError, "Every installed input"):
            self.prepare()
        self.runtime["attempts"][0]["readonly_inputs"] += self.installed
        self.save()
        Path(self.plan["candidate"]["path"]).write_text("Changed frozen source manifest")
        with self.assertRaisesRegex(native.NativeProcessError, "Pinned bytes changed"):
            self.prepare()

    def test_added_discovery_config_invalidates_the_frozen_inventory(self):
        request = self.prepare()
        (self.workspace / ".codex" / "config.toml").write_text("model='unallocated-model'\n")
        with self.assertRaisesRegex(native.NativeProcessError, "discovery inventory changed"):
            native._fresh(request)

    def test_immutable_discovery_mount_refuses_additions_but_allows_ordinary_outputs(self):
        request = self.prepare()
        request["system_mounts"] += ["/usr/lib", "/lib", "/lib64"]
        code = ("from pathlib import Path\n"
                "try:\n Path(" + repr(str(self.workspace / ".codex" / "config.toml")) + ").write_text('changed')\n"
                "except OSError:\n print('blocked')\n"
                "Path(" + repr(str(self.workspace / "ordinary-output.txt")) + ").write_text('allowed')\n")
        request["command"] = ["/usr/bin/python3", "-I", "-c", code]
        # The unsigned fixture helper cannot sign this deliberately substituted command.
        result = native.run_fixture(native.sandbox_command(request), timeout=3)
        self.assertEqual(result["process"]["status"], "EXITED", result["stderr"])
        self.assertEqual(result["stdout"], b"blocked\n")
        self.assertEqual((self.workspace / "ordinary-output.txt").read_text(), "allowed")
        self.assertFalse(result["native_valid"])


class ManagedBrokerTests(unittest.TestCase):
    """Real local broker/phase fixtures, no native client or native receipt."""

    def setUp(self):
        from test_utility_state import Fixture
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = Fixture(self.root)
        self.fixture.session["deadline_utc"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.fixture.write_contracts()
        self.request = {"workspace": str(self.fixture.project), "managed_worker": {
            "session": pin(self.fixture.session_path), "state": str(self.fixture.state),
            "gate_executable": {"path": "/not/executed/in/this/fixture", "sha256": "a" * 64}}}
        self.evidence = self.root / "broker-evidence"
        self.broker = native._managed_start(self.request, self.evidence)
        self.addCleanup(lambda: self.broker.close() if self.broker.socket_root.exists() else None)

    def callback(self, name, **fields):
        event = {"hook_event_name": name, "session_id": "fixture-session", "cwd": str(self.fixture.project)}
        event.update(fields)
        if name == "Stop":
            event["stop_hook_active"] = False
        request = {"provider": "codex", "contract_sha256": self.broker.contract_digest, "event": event}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(self.broker.socket_path))
            connection.sendall(native.canonical_json(request) + b"\n")
            raw = bytearray()
            while not raw.endswith(b"\n"):
                raw.extend(connection.recv(65536))
        return json.loads(raw)

    def test_actual_callbacks_drive_mechanical_completion_but_retain_unauthenticated_origin(self):
        self.callback("SessionStart")
        self.callback("UserPromptSubmit")
        for _ in self.fixture.delivery["phases"]:
            self.fixture.checkpoint()
            self.callback("Stop")
        self.callback("SessionEnd")
        result = native._managed_finish(self.broker, self.evidence)
        self.assertEqual(result["status"], "COMPLETED", result)
        self.assertTrue(result["broker_quiescent"])
        self.assertTrue(result["task_result"]["receipt_verified"])
        self.assertEqual(result["callback_origin"], "NOT_AUTHENTICATED")
        self.assertGreater(len(result["evidence"]), 3)
        self.assertNotIn("hmac_sha256", result)

    def test_managed_counted_continuation_correlates_waiting_phase_and_exact_prompts(self):
        from test_utility_state import Fixture
        self.broker.close()
        root = self.root / "multi"
        root.mkdir()
        self.fixture = Fixture(root)
        self.fixture.session["deadline_utc"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.fixture.delivery["questions"] = [{"id": "Q-001", "phase": "Intake", "question": "Choose repository",
            "blocking_dependency": "Need repository", "choices": ["Use repository A", "Use repository B"], "decision_path": None}]
        self.fixture.write_contracts()
        request = {"workspace": str(self.fixture.project), "managed_worker": {
            "session": pin(self.fixture.session_path), "state": str(self.fixture.state),
            "gate_executable": {"path": "/not/executed", "sha256": "a" * 64}}}
        self.evidence = root / "events"
        self.broker = native._managed_start(request, self.evidence)
        self.broker.native_sent_prompts = ["initial", "Use repository A"]
        self.callback("SessionStart")
        self.callback("UserPromptSubmit", prompt="initial")
        value = self.fixture.checkpoint()
        value.update(state="awaiting_user", evidence=[], question_id="Q-001")
        self.fixture.checkpoint_path.write_bytes(native.canonical_json(value))
        self.callback("Stop")
        self.assertEqual(self.broker.engine.context(self.broker.state)["status"], "WAITING_USER")
        self.callback("UserPromptSubmit", prompt="Use repository A")
        for _ in self.fixture.delivery["phases"]:
            self.fixture.checkpoint()
            self.callback("Stop")
        self.callback("SessionEnd")
        result = native._managed_finish(self.broker, self.evidence)
        self.assertEqual(result["status"], "COMPLETED", result)
        self.assertTrue(result["task_result"]["receipt_verified"])
        self.broker.native_sent_prompts = ["initial", "Use repository B"]
        self.assertEqual(native._managed_finish(self.broker, self.evidence)["status"], "COULD_NOT_RUN")

    def test_extra_prompt_without_waiting_transition_cannot_complete_managed_evidence(self):
        self.broker.native_sent_prompts = ["initial", "extra"]
        self.callback("SessionStart")
        self.callback("UserPromptSubmit", prompt="initial")
        self.callback("UserPromptSubmit", prompt="extra")
        for _ in self.fixture.delivery["phases"]:
            self.fixture.checkpoint()
            self.callback("Stop")
        self.callback("SessionEnd")
        result = native._managed_finish(self.broker, self.evidence)
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertTrue(self.fixture.receipt.exists())

    def test_missing_callbacks_do_not_create_a_mechanical_receipt(self):
        result = native._managed_finish(self.broker, self.evidence)
        self.assertEqual(result["status"], "ACTIVE")
        self.assertIsNone(result["task_result"])
        self.assertFalse(self.fixture.receipt.exists())

    def test_wrong_session_or_malformed_hook_prevents_completion(self):
        reply = self.callback("Stop")  # Missing SessionStart is an actual broker rejection.
        self.assertIs(reply.get("continue"), False)
        result = native._managed_finish(self.broker, self.evidence)
        self.assertEqual(result["status"], "COULD_NOT_RUN")
        self.assertTrue(result["broker_quiescent"])
        self.assertFalse(self.fixture.receipt.exists())


if __name__ == "__main__":
    unittest.main()
