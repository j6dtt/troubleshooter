"""python -m unittest discover tests  (no network, no Docker needed)"""
import dataclasses
import gzip
import hashlib
import io
import json
import os
import ssl
import tempfile
import contextlib
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from unittest import mock

from troubleshooter import anonymize, autotrace, cases, cli, config, engine, history, hosts, launcher, learn, links, llm, tools, watch, web, webpage
from troubleshooter.config import Settings
from troubleshooter.redact import clean, mask, strip_control
from troubleshooter.run import run


ONE = {"a": tools.Cluster("a", "/k/a", "ctx-a")}
TWO = {**ONE, "b": tools.Cluster("b", "/k/b", "ctx-b")}


class Masking(unittest.TestCase):
    def test_secrets_are_masked(self):
        for text, secret in [
            ("DB_PASSWORD=abc123", "abc123"), ('"MYSQL_ROOT_PASSWORD=hunter2",', "hunter2"),
            ('password: "a b c"', "a b c"), ('{"name":"API_TOKEN","value":"zz9"}', "zz9"),
            ("- name: DB_PASS\n  value: s3cret", "s3cret"), ("postgres://u:pw99@db:5432/x", "pw99"),
            ("Authorization: Bearer abcdefgh123", "abcdefgh123"), ("client-key-data: QUJD", "QUJD"),
            ('\\"password\\":\\"esc1\\"', "esc1"), ("curl -H 'X-Api-Key: k3y9' x", "k3y9"),
            ("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----", "AAAA"),
            ("eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl", "c2lnbmF0dXJl"),
        ]:
            self.assertNotIn(secret, mask(text), text)

    def test_useful_values_survive(self):
        for text in ("secretName: tls-cert", "PASSWORD_FILE=/run/secrets/x", "Passed: 5 tests",
                     "ERROR connection refused to db:5432", "image: nginx:1.27"):
            self.assertEqual(mask(text), text)

    def test_terminal_codes_removed(self):
        self.assertEqual(strip_control("a\x1b[31mred\x1b[0m\x1b]0;title\x07b\x07"), "aredb")

    def test_long_output_keeps_the_end(self):
        out = clean("\n".join(f"line {i}" for i in range(5000)), 500)
        self.assertIn("line 4999", out)
        self.assertIn("lines cut", out)


class ThinkFiltering(unittest.TestCase):
    def test_split_tags(self):
        f = llm.ThinkFilter()
        out = "".join(f.feed(p) for p in ["Hi <th", "ink>secret</thi", "nk> there <", "b>"]) + f.flush()
        self.assertEqual(out, "Hi  there <b>")

    def test_strip(self):
        self.assertEqual(llm.strip_think("<think>x</think>answer"), "answer")
        self.assertEqual(llm.strip_think("reasoning</think>answer"), "answer")


class Arguments(unittest.TestCase):
    def setUp(self):
        self.box = tools.Toolbox(Settings(), clusters=ONE)

    def test_option_injection_refused(self):
        for bad in ("--privileged", "-v", "a b", "a;rm", "$(x)", "a/b", ""):
            with self.assertRaises(tools.BadInput):
                tools.name(bad)

    def test_docker_style_names_accepted(self):
        self.assertEqual(tools.name("uhu-bhr.13.3"), "uhu-bhr.13.3")

    def test_since(self):
        self.assertEqual(tools.since("1d"), "24h")
        with self.assertRaises(tools.BadInput):
            tools.since("--foo")

    def test_secrets_kind_refused(self):
        for kind in ("secrets", "secret", "Secrets", "secrets.v1"):
            with self.assertRaises(tools.BadInput):
                self.box.k8s_get(None, kind, None, None)
            with self.assertRaises(tools.BadInput):
                self.box.k8s_describe(None, kind, "x", None)
        for kind in ("pods,secrets", "secrets/x"):
            with self.assertRaises(tools.BadInput):
                self.box.k8s_get(None, kind, None, None)

    def test_one_cluster_needs_no_name(self):
        self.assertIn("ctx-a", self.box._kubectl(None, "get", "pods"))

    def test_active_context_is_the_default(self):
        box = tools.Toolbox(Settings(), clusters=TWO)
        self.assertIn("ctx-a", box.k8s_delete_pod(None, "p", "n").argv)
        with self.assertRaises(tools.BadInput):
            box.k8s_delete_pod("nosuch", "p", "n")
        self.assertIn("ctx-b", box.k8s_delete_pod("b", "p", "n").argv)
        self.assertIn("cluster b", box.k8s_delete_pod("b", "p", "n").title)

    def test_switching_context(self):
        box = tools.Toolbox(Settings(), clusters=TWO)
        box.k8s_use_context("b")
        self.assertIn("ctx-b", box._kubectl(None, "get", "pods"))
        with self.assertRaises(tools.BadInput):
            box.k8s_use_context("nosuch")
        self.assertEqual(box.active, "b")

    def test_context_namespace_is_the_default(self):
        c = {"me": tools.Cluster("me", "", "me", server="http://x", namespace="team1")}
        box = tools.Toolbox(Settings(), clusters=c)
        self.assertIn("team1", box.k8s_delete_pod(None, "p", None).argv)

    def test_proxies_read_from_the_launcher(self):
        with mock.patch.dict(os.environ, {"KUBE_PROXIES": "a:b|1234|team1;c|99|", "KUBE_CURRENT": "c"}):
            found = tools.find_clusters("/none")
            box = tools.Toolbox(Settings(kube_dir="/none"))
        self.assertEqual(found["a-b"].server, "http://127.0.0.1:1234")
        self.assertEqual(found["a-b"].namespace, "team1")
        self.assertEqual(box.active, "c")

    def test_own_login_goes_through_the_proxy(self):
        c = {"me": tools.Cluster("me", "", "me", server="http://127.0.0.1:9")}
        box = tools.Toolbox(Settings(), clusters=c)
        argv = box._kubectl(None, "get", "pods")
        self.assertIn("http://127.0.0.1:9", argv)
        self.assertNotIn("--context", argv)

    def test_protected_namespace(self):
        box = tools.Toolbox(Settings(protected_names=("kube-system", "vmware-system-*")), clusters=ONE)
        for ns in ("kube-system", "vmware-system-csi"):
            for change in (lambda: box.k8s_delete_pod(None, "p", ns),
                           lambda: box.k8s_restart(None, "deployments", "d", ns),
                           lambda: box.k8s_scale(None, "deployments", "d", ns, 0)):
                with self.assertRaises(tools.BadInput):
                    change()

    def test_kube_errors_are_short(self):
        self.assertIn("expired", tools.kube_error('E1106 memcache.go noise\nerror: You must be logged in to the server (Unauthorized)'))
        self.assertEqual(tools.kube_error("noise\nUnable to connect"), "Unable to connect")

    def test_no_clusters_hides_k8s_tools(self):
        box = tools.Toolbox(Settings(), clusters={})
        self.assertFalse([n for n in box.tools if n.startswith("k8s") or n == "pod_check"])

    def test_unknown_argument_refused(self):
        with self.assertRaises(tools.BadInput):
            self.box.tools["docker_logs"].check({"container": "x", "shell": "sh"})

    def test_image_names(self):
        for bad in ("--privileged", "a b", "a;b", "$(x)"):
            with self.assertRaises(tools.BadInput):
                self.box.docker_images(bad)

    def test_net_test_arguments(self):
        for container, host, port in (("--x", "h", 80), ("c", "-h", 80), ("c", "h;id", 80), ("c", "h", 0), ("c", "h", 70000)):
            with self.assertRaises(tools.BadInput):
                self.box.container_net_test(container, host, port)

    def test_no_free_command_tool(self):
        self.assertFalse([n for n in self.box.tools if n in ("shell", "exec", "run", "bash")])

    def test_protected_never_changes(self):
        box = tools.Toolbox(Settings(protected_names=("troubleshooter", "db-*")))
        for target in ("troubleshooter", "db-1"):
            with self.assertRaises(tools.BadInput):
                box.docker_restart(target)

    def test_look_only_mode_has_no_change_tools(self):
        box = tools.Toolbox(Settings(allow_changes=False))
        self.assertFalse([t for t in box.tools.values() if t.change])


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        r = self.dir.name
        os.makedirs(f"{r}/apps/x")
        os.makedirs(f"{r}/etc")
        for p in ("apps/x/app.log", "apps/x/.env", "apps/x/tls.key", "etc/passwd"):
            with open(f"{r}/{p}", "w") as f:
                f.write("DB_PASSWORD=abc\nhello\n")
        os.symlink(f"{r}/etc/passwd", f"{r}/apps/x/link")
        self.box = tools.Toolbox(Settings(host_root=r, file_roots=("/apps",)))

    def tearDown(self):
        self.dir.cleanup()

    def test_allowed(self):
        self.assertIn("hello", self.box.read_file("/apps/x/app.log", None, None))

    def test_kubeconfig_never_read(self):
        with open(f"{self.dir.name}/apps/x/c.yaml", "w") as f:
            f.write("apiVersion: v1\nkind: Config\nusers: []\n")
        with self.assertRaises(tools.BadInput):
            self.box.read_file("/apps/x/c.yaml", None, None)

    def test_read_by_line_range(self):
        with open(f"{self.dir.name}/apps/x/big.py", "w") as f:
            f.write("".join(f"row{i}\n" for i in range(1, 1001)))
        one = self.box.read_file("/apps/x/big.py", None, None, 500, 502)
        self.assertEqual(one.splitlines()[0], "500: row500")
        self.assertIn("502: row502", one)
        first = self.box.read_file("/apps/x/big.py", None, None)
        self.assertIn("start_line=301", first)
        self.assertIn("700: row700", self.box.read_file("/apps/x/big.py", None, "row700"))

    def test_search_finds_code_and_skips_secrets(self):
        r = self.dir.name
        os.makedirs(f"{r}/apps/x/node_modules")
        for p, text in (("apps/x/main.py", "def charge_card():\n"), ("apps/x/node_modules/lib.js", "charge_card\n"),
                        ("apps/x/.env", "charge_card=1\n"), ("apps/x/tls.key", "charge_card\n"),
                        ("apps/x/kc.yaml", "kind: Config\ncharge_card\n")):
            with open(f"{r}/{p}", "w") as f:
                f.write(text)
        os.symlink(f"{r}/etc/passwd", f"{r}/apps/x/l.py")
        got = self.box.search_code("/apps/x", "charge_card", None)
        self.assertEqual(got, "/apps/x/main.py:1: def charge_card():")
        self.assertEqual(self.box.search_code("/apps/x", "hello", ".py"), "Nothing found in 1 files.")

    def test_search_stays_inside_roots(self):
        with self.assertRaises(tools.BadInput):
            self.box.search_code("/etc", "root", None)

    def test_refused(self):
        for path in ("/etc/passwd", "/apps/../etc/passwd", "/apps/x/.env", "/apps/x/tls.key", "/apps/x/link"):
            with self.assertRaises(tools.BadInput, msg=path):
                self.box.read_file(path, None, None)


class PastedText(unittest.TestCase):
    def test_secrets_in_pasted_text_are_masked(self):
        sess = engine.Session(Settings(llm_url="http://x", llm_model="m", kube_dir="/none"), UI())
        with mock.patch.object(llm, "chat", lambda *a, **k: ({"role": "assistant", "content": "ok"}, "stop")):
            sess.ask("DB_PASSWORD=hunter22")
        self.assertNotIn("hunter22", str(sess.messages))


class UI:
    def __init__(self, approve=False):
        self.answer, self.asked, self.notices, self.steps = approve, [], [], []

    def text(self, t): pass
    def step(self, t): self.steps.append(t)
    def notice(self, t): self.notices.append(t)

    def approve(self, title, reason, command):
        self.asked.append(command)
        return self.answer


def call(tool, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}




class Gate(unittest.TestCase):
    def go(self, replies, ui, **kw):
        s = Settings(llm_url="http://x", llm_model="m", kube_dir="/none", **kw)
        it = iter(replies)
        sess = engine.Session(s, ui)
        with mock.patch.object(llm, "chat", lambda *a, **k: (next(it), "stop")), \
                mock.patch.object(engine, "run") as ran:
            ran.return_value = mock.Mock(ok=True, text="ok")
            sess.ask("q")
        return sess, ran

    def done(self):
        return {"role": "assistant", "content": "finished"}

    def test_declined_change_never_runs(self):
        ui = UI(False)
        sess, ran = self.go([call("docker_restart", container="web", reason="r"), self.done()], ui)
        self.assertEqual(ui.asked, ["docker restart web"])
        ran.assert_not_called()
        self.assertIn("declined", [m for m in sess.messages if m["role"] == "tool"][0]["content"])

    def test_approved_change_runs_exactly(self):
        ui = UI(True)
        _, ran = self.go([call("docker_restart", container="web", reason="r"), self.done()], ui)
        ran.assert_called_once_with(["docker", "restart", "web"], timeout=90)

    def test_protected_never_reaches_approval(self):
        ui = UI(True)
        _, ran = self.go([call("docker_stop", container="troubleshooter", reason="r"), self.done()],
                         ui, protected_names=("troubleshooter",))
        self.assertEqual(ui.asked, [])
        ran.assert_not_called()

    def test_bad_calls_return_errors_to_the_model(self):
        ui = UI()
        bad = call("nope")
        broken = call("docker_logs")
        broken["tool_calls"][0]["function"]["arguments"] = "{not json"
        sess, _ = self.go([bad, broken, self.done()], ui)
        got = [m["content"] for m in sess.messages if m["role"] == "tool"]
        self.assertIn("No such tool", got[0])
        self.assertIn("not valid JSON", got[1])

    def test_step_limit_ends_with_a_summary(self):
        ui = UI()
        replies = [call("docker_logs", container=f"c{i}") for i in range(3)] + [self.done()]
        sess, _ = self.go(replies, ui, max_steps=3)
        self.assertEqual(sess.messages[-1]["content"], "finished")
        self.assertTrue(ui.notices)

    def test_time_limit_ends_with_a_summary(self):
        ui = UI()
        sess, _ = self.go([self.done()], ui, max_seconds=-1)
        self.assertEqual(sess.messages[-1]["content"], "finished")
        self.assertTrue(ui.notices)

    def test_model_failure_keeps_conversation_usable(self):
        ui = UI()
        sess = engine.Session(Settings(llm_url="http://x", llm_model="m", kube_dir="/none"), ui)
        with mock.patch.object(llm, "chat", side_effect=llm.LLMError("down")):
            sess.ask("q")
        self.assertEqual(sess.messages[-1]["role"], "assistant")
        self.assertEqual(ui.notices, ["down"])

    def test_old_output_dropped_when_too_long(self):
        sess = engine.Session(Settings(max_context_chars=100, kube_dir="/none"), UI())
        sess.messages += [{"role": "tool", "tool_call_id": "1", "content": "x" * 5000}]
        sess._trim()
        self.assertEqual(sess.messages[-1]["content"], engine.STUB)


class CertSetting(unittest.TestCase):
    def test_on_and_off(self):
        off = llm._ssl_context(Settings(llm_url="https://x", llm_verify_tls=False))
        on = llm._ssl_context(Settings(llm_url="https://x", llm_verify_tls=True))
        self.assertEqual(off.verify_mode, ssl.CERT_NONE)
        self.assertEqual(on.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(on.check_hostname)
        self.assertIsNone(llm._ssl_context(Settings(llm_url="http://x")))

    def test_missing_ca_file_is_explained(self):
        with self.assertRaises(llm.LLMError):
            llm._ssl_context(Settings(llm_url="https://x", llm_verify_tls=True, llm_ca_file="/none"))


class Retry(unittest.TestCase):
    def test_waits_out_busy_then_gives_up(self):
        s = Settings(llm_url="http://x", llm_model="m", llm_busy_wait=10)
        waits = []
        with mock.patch.object(llm, "_once", side_effect=llm.Busy("busy")):
            with self.assertRaises(llm.LLMError):
                llm.chat(s, [], on_wait=waits.append, sleep=lambda n: None)
        self.assertEqual(len(waits), 1)

    def test_gpt5_parameters(self):
        body = llm._body(Settings(llm_model="gpt-5.4", llm_reasoning="off"), [], None)
        self.assertIn("max_completion_tokens", body)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(body["reasoning_effort"], "none")  # gpt-5.4 refuses "minimal"

    def test_first_gpt5_still_says_minimal(self):
        body = llm._body(Settings(llm_model="gpt-5", llm_reasoning="off"), [], None)
        self.assertEqual(body["reasoning_effort"], "minimal")

    def test_404_says_where_and_what_the_server_said(self):
        s = Settings(llm_url="https://gw/v1", llm_model="gpt-5.4")
        text = llm._explain(404, '{"error": {"message": "The model gpt-5.4 does not exist"}}', s, "/chat/completions")
        self.assertIn("https://gw/v1/chat/completions", text)
        self.assertIn("does not exist", text)

    def test_server_reason_is_masked(self):
        text = llm._explain(401, '{"error": "bad api_key=sk-abcdefghijklmnopqrstuvwxyz"}', Settings())
        self.assertNotIn("abcdefghijklmnop", text)

    def test_reasoning_refusal_says_which_setting(self):
        s = Settings(llm_model="gpt-5.4", llm_reasoning="high")
        detail = '{"error": {"message": "Function tools with reasoning_effort are not supported for gpt-5.4"}}'
        self.assertIn("Leave llm-reasoning empty", llm._explain(400, detail, s))

    def test_check_goes_on_when_the_model_list_is_missing(self):
        def missing(s):
            err = llm.LLMError("Not found")
            err.code = 404
            raise err
        called = {"overview": True}
        out = io.StringIO()
        with mock.patch.object(llm, "models", missing), mock.patch.object(llm, "context_tokens", lambda s: None), \
                mock.patch.object(llm, "chat", lambda *a, **k: ({"tool_calls": [called]}, "tool_calls")), \
                redirect_stdout(out):
            cli.check(Settings(llm_url="https://gw/v1", llm_model="gpt-5.4"))
        self.assertIn("[warn] Model list", out.getvalue())
        self.assertIn("[ok] Tool calling", out.getvalue())


class KubectlWarnings(unittest.TestCase):
    """kubectl writes version-skew warnings to stderr, after its JSON."""

    def fake(self, body):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "fakecmd")
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)
        return p

    def test_warning_stays_out_of_parsed_output(self):
        p = self.fake("echo '{\"a\": 1}'; echo 'WARNING: version difference' >&2")
        self.assertEqual(json.loads(run([p], parsed=True).text), {"a": 1})

    def test_text_tools_still_see_stderr(self):
        p = self.fake("echo out; echo err >&2")
        self.assertIn("err", run([p]).text)

    def test_failure_keeps_the_error_message(self):
        p = self.fake("echo 'pods is forbidden' >&2; exit 1")
        r = run([p], parsed=True)
        self.assertFalse(r.ok)
        self.assertIn("forbidden", r.text)

    def test_check_survives_skew_warning(self):
        skew = self.fake(
            """case "$*" in
*version*) echo '{"clientVersion":{"minor":"34"},"serverVersion":{"minor":"31+"}}'
  echo 'WARNING: version difference between client (1.34) and server (1.31)' >&2;;
*can-i*) echo no;;
*) echo node1;;
esac""")
        os.rename(skew, os.path.join(os.path.dirname(skew), "kubectl"))
        box = tools.Toolbox.__new__(tools.Toolbox)
        c = tools.Cluster("vks", "", "vks")
        box.clusters = {"vks": c}
        box.active = "vks"
        out = io.StringIO()
        path = os.path.dirname(skew) + os.pathsep + os.environ["PATH"]
        with mock.patch.dict(os.environ, {"PATH": path}), redirect_stdout(out):
            cli.check_cluster(box, c, lambda *a: None)
        self.assertIn("1.34 and cluster 1.31", out.getvalue())


def vks_item(name, labels=None, spec=None, status=None, ns="team-a"):
    return {"metadata": {"name": name, "namespace": ns, "labels": labels or {}}, "spec": spec or {}, "status": status or {}}


OF = {"cluster.x-k8s.io/cluster-name": "prod1"}
NO_VM = {"type": "VirtualMachineCreated", "status": "False", "reason": "NotEnoughResources",
         "message": "no host has enough memory"}
VKS = {
    "clusters.cluster.x-k8s.io": [vks_item("prod1", spec={"topology": {"version": "v1.33.1"}}, status={"phase": "Provisioned"}),
                                  vks_item("other", status={"phase": "Provisioned"})],
    "kubeadmcontrolplanes.controlplane.cluster.x-k8s.io": [vks_item("prod1-cp", OF, {"replicas": 3}, {"readyReplicas": 3})],
    "machinedeployments.cluster.x-k8s.io": [vks_item("prod1-pool", OF, {"replicas": 2}, {"readyReplicas": 1})],
    "machines.cluster.x-k8s.io": [
        vks_item("prod1-cp-1", OF, {"clusterName": "prod1"}, {"phase": "Running", "nodeRef": {"name": "prod1-cp-1"}}),
        vks_item("prod1-w-1", OF, {"clusterName": "prod1"}, {"phase": "Running", "nodeRef": {"name": "prod1-w-1"}}),
        vks_item("prod1-w-2", OF, {"clusterName": "prod1", "infrastructureRef": {"name": "prod1-w-2"}},
                 {"phase": "Provisioning", "conditions": [
                     {"type": "InfrastructureReady", "status": "False", "reason": "WaitingForInfrastructure"},
                     {"type": "Deleting", "status": "False"}]})],
    "virtualmachines.vmoperator.vmware.com": [
        vks_item("prod1-cp-1", status={"powerState": "PoweredOn", "network": {"primaryIP4": "10.0.0.5"}}),
        vks_item("prod1-w-1", status={"powerState": "poweredOn", "vmIp": "10.0.0.6"}),
        vks_item("prod1-w-2", status={"powerState": "PoweredOff", "conditions": [NO_VM]})],
}


class Vks(unittest.TestCase):
    """A fake kubectl: the context named super offers the Cluster API, the one named work does not."""

    def setUp(self):
        self.calls = []
        self.forbid_all = False
        self.box = tools.Toolbox(Settings(), clusters={
            "work": tools.Cluster("work", "", "work"), "super": tools.Cluster("super", "", "super", namespace="team-a")})
        patch = mock.patch.object(tools, "run", self.kubectl)
        patch.start()
        self.addCleanup(patch.stop)

    def kubectl(self, argv, timeout=30, parsed=False):
        self.calls.append(argv)
        supervisor = argv[argv.index("--context") + 1] == "super"
        if "/version" in argv:
            return self.say("{}")
        if "/apis/cluster.x-k8s.io" in argv:
            return self.say("{}") if supervisor else self.fail_with("Error from server (NotFound): the server could not find the requested resource")
        if "events" in argv:
            return self.say("LAST SEEN  TYPE  REASON\n2m  Warning  CreateFailed  no host has enough memory")
        kind = argv[argv.index("get") + 1]
        if not supervisor:
            return self.fail_with(f'error: the server doesn\'t have a resource type "{kind}"')
        if self.forbid_all and "--all-namespaces" in argv:
            return self.fail_with(f"Error from server (Forbidden): {kind} is forbidden")
        return self.say(json.dumps({"items": VKS[kind]}))

    @staticmethod
    def say(text):
        return type("R", (), {"ok": True, "text": text})()

    @staticmethod
    def fail_with(text):
        return type("R", (), {"ok": False, "text": text})()

    def test_contexts_mark_the_supervisor(self):
        rows = self.box.k8s_contexts().splitlines()
        self.assertEqual(rows[0], "* work: ok")
        self.assertIn("super (namespace team-a): ok, supervisor", rows[1])

    def test_role_is_asked_once(self):
        self.box.k8s_contexts()
        self.box.k8s_contexts()
        self.assertEqual(sum("/apis/cluster.x-k8s.io" in c for c in self.calls), 2)  # once per context

    def test_finds_the_supervisor_from_a_workload_context(self):
        out = self.box.vks_cluster_health(None, None, "prod1")
        self.assertIn("Asked the Supervisor context super; the active context is work", out)
        self.assertEqual(self.box.active, "work")  # looking does not switch

    def test_reports_only_what_is_wrong_with_the_reason(self):
        out = self.box.vks_cluster_health(None, None, "prod1")
        self.assertIn("VKS CLUSTER team-a/prod1 (v1.33.1): 2 of 3 machines running; 3 need a look", out)
        self.assertIn("- worker pool prod1-pool: 1 of 2 ready", out)
        self.assertIn("- machine prod1-w-2 (node no node yet): phase Provisioning; "
                      "InfrastructureReady=False (WaitingForInfrastructure)", out)
        self.assertIn("  - VM prod1-w-2: power PoweredOff; no IP address; "
                      "VirtualMachineCreated=False (NotEnoughResources no host has enough memory)", out)
        self.assertIn("Newest warning events in namespace team-a:", out)
        for healthy in ("prod1-cp-1", "prod1-w-1", "control plane", "Deleting", "other"):
            self.assertNotIn(healthy, out)

    def test_healthy_cluster_is_one_line(self):
        out = self.box.vks_cluster_health("super", None, "other")
        self.assertIn("VKS CLUSTER team-a/other: 0 of 0 machines running; nothing wrong found", out)

    def test_namespace_login_falls_back_to_its_own_namespace(self):
        self.forbid_all = True
        out = self.box.vks_cluster_health("super", None, None)
        self.assertIn("2 VKS cluster(s)", out)
        self.assertTrue(any(c[-2:] == ["-n", "team-a"] for c in self.calls))

    def test_workload_context_is_told_plainly(self):
        self.assertIn("Could not list VKS clusters in context work", self.box.vks_cluster_health("work", None, None))

    def test_no_supervisor_at_all(self):
        box = tools.Toolbox(Settings(), clusters={"work": tools.Cluster("work", "", "work")})
        with self.assertRaises(tools.BadInput) as e:
            box.vks_cluster_health(None, None, None)
        self.assertIn("log in to the Supervisor", str(e.exception))

    def test_bad_names_refused(self):
        for args in ((None, "a;b", None), (None, None, "--all")):
            with self.assertRaises(tools.BadInput):
                self.box.vks_cluster_health(*args)

    def test_reachable_through_the_tool_list(self):
        t = self.box.tools["vks_cluster_health"]
        self.assertIn("VKS CLUSTER team-a/prod1", t.fn(**t.check({"name": "prod1"})))
        self.assertEqual(t.describe({"name": "prod1"}), "Checked VKS cluster health prod1")


def reply(ok, text):
    return type("R", (), {"ok": ok, "text": text})()


def pod(name, ready=True, node="n1", labels=None, ports=(8080,), restarts=0, ended=None):
    box = {"name": "app", "ready": ready, "restartCount": restarts, "state": {"running": {}} if ready else {"waiting": {"reason": "CrashLoopBackOff"}}}
    if ended:
        box["lastState"] = {"terminated": {"finishedAt": ended, "reason": "OOMKilled", "exitCode": 137}}
    return {"metadata": {"name": name, "namespace": "shop", "labels": labels or {"app": "web"}},
            "spec": {"nodeName": node, "containers": [{"name": "app", "ports": [{"containerPort": p} for p in ports]}]},
            "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                       "containerStatuses": [box]}}


class KubeTools(unittest.TestCase):
    """Tools that follow Kubernetes objects, against canned kubectl answers."""

    def setUp(self):
        self.calls, self.answers = [], {}
        self.box = tools.Toolbox(Settings(), clusters=ONE)
        patch = mock.patch.object(tools, "run", self.kubectl)
        patch.start()
        self.addCleanup(patch.stop)

    def kubectl(self, argv, timeout=30, parsed=False):
        self.calls.append(argv)
        for words, answer in self.answers.items():
            if all(w in argv for w in words.split()):
                return reply(True, answer if isinstance(answer, str) else json.dumps(answer))
        return reply(False, "Error from server (NotFound): not found")

    def service(self, target=8080, kind="ClusterIP", selector=None):
        self.answers["services web"] = {"spec": {"type": kind, "clusterIP": "10.96.0.9", "selector": selector or {"app": "web"},
                                                 "ports": [{"port": 80, "targetPort": target}]}, "status": {}}

    def test_service_with_no_matching_pods(self):
        self.service()
        self.answers["endpointslices"] = {"items": []}
        self.answers["pods"] = {"items": []}
        out = self.box.k8s_service_check(None, "web", "shop")
        self.assertIn("SERVICE shop/web: type ClusterIP, cluster IP 10.96.0.9, ports 80 to 8080", out)
        self.assertIn("- no ready endpoints", out)
        self.assertIn("- the selector app=web matches no pods in namespace shop", out)

    def test_service_names_the_broken_pod_and_node(self):
        self.service()
        self.answers["endpointslices"] = {"items": [{"endpoints": [{"conditions": {"ready": True}}, {"conditions": {"ready": False}}]}]}
        self.answers["pods"] = {"items": [pod("web-1"), pod("web-2", ready=False, node="n2")]}
        self.answers["nodes"] = {"items": [
            {"metadata": {"name": "n1"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
            {"metadata": {"name": "n2"}, "status": {"conditions": [{"type": "Ready", "status": "Unknown"}]}}]}
        out = self.box.k8s_service_check(None, "web", "shop")
        self.assertIn("Endpoints: 1 ready, 1 not ready", out)
        self.assertIn("Pods matching app=web: 2, of which 1 ready", out)
        self.assertIn("- pod web-2 on node n2: CrashLoopBackOff", out)
        self.assertIn("- node n2, which runs some of these pods: Ready=Unknown", out)
        self.assertNotIn("web-1", out)

    def test_service_port_mismatches(self):
        self.answers["endpointslices"] = {"items": [{"endpoints": [{"conditions": {"ready": True}}]}]}
        self.answers["pods"] = {"items": [pod("web-1")]}
        self.service(target=9000)
        self.assertIn("targetPort 9000 is not a port the pods declare (8080)", self.box.k8s_service_check(None, "web", "shop"))
        self.service(target="http")
        self.assertIn("targetPort 'http' is a port name that no pod defines", self.box.k8s_service_check(None, "web", "shop"))
        self.service()
        self.assertIn("Nothing wrong found in the chain", self.box.k8s_service_check(None, "web", "shop"))

    def test_load_balancer_without_an_address(self):
        self.service(kind="LoadBalancer")
        self.answers["endpointslices"] = {"items": [{"endpoints": [{"conditions": {"ready": True}}]}]}
        self.answers["pods"] = {"items": [pod("web-1")]}
        self.assertIn("- no external address yet", self.box.k8s_service_check(None, "web", "shop"))

    def test_workload_logs_use_the_workloads_own_selector(self):
        self.answers["deployments web"] = {"spec": {"selector": {"matchLabels": {"app": "web", "tier": "front"}}}}
        self.answers["logs"] = "[pod/web-1/app] 2026 started\n[pod/web-2/app] 2026 ERROR no database"
        out = self.box.k8s_workload_logs(None, "deployments", "web", "shop", None, "2h", "error", False)
        self.assertEqual(out, "[pod/web-2/app] 2026 ERROR no database")
        sent = self.calls[-1]
        self.assertEqual(sent[sent.index("-l") + 1], "app=web,tier=front")
        for word in ("--prefix", "--tail=50", "--since=2h", "-n", "shop"):
            self.assertIn(word, sent)

    def test_pod_net_test_passes_host_and_port_as_arguments(self):
        self.answers["exec"] = "DNS: db is 10.0.0.9\nTCP: connected to db port 5432 (tested with nc)"
        self.assertIn("TCP: connected", self.box.pod_net_test(None, "web-1", "shop", "db", 5432, None))
        sent = self.calls[-1]
        self.assertEqual(sent[-5:-2], ["-c", tools.POD_NET_TEST, "sh"])
        self.assertEqual(sent[-2:], ["db", "5432"])
        self.assertNotIn("db", tools.POD_NET_TEST)
        for host, port in (("a;rm -rf /", 80), ("$(id)", 80), ("db", 0), ("db", 70000)):
            with self.assertRaises(tools.BadInput):
                self.box.pod_net_test(None, "web-1", "shop", host, port, None)

    def test_pod_without_a_shell(self):
        self.answers["exec"] = 'OCI runtime exec failed: exec: "sh": executable file not found in $PATH'
        self.assertIn("has no shell", self.box.pod_net_test(None, "web-1", "shop", "db", 5432, None))

    def test_the_net_test_script_runs(self):
        patch_off = run(["sh", "-c", tools.POD_NET_TEST, "sh", "127.0.0.1", "1"])
        self.assertIn("TCP: could not connect to 127.0.0.1 port 1", patch_off.text)


class VksVolumes(unittest.TestCase):
    """The claim lives in the workload context; VMware's copy of it lives in the Supervisor."""

    UID = "1111-2222"
    HANDLE = "cluster-uid-1111-2222"

    def setUp(self):
        self.work, self.sup, self.calls = {}, {}, []
        self.box = tools.Toolbox(Settings(), clusters={
            "work": tools.Cluster("work", "", "work"), "super": tools.Cluster("super", "", "super", namespace="team-a")})
        patch = mock.patch.object(tools, "run", self.kubectl)
        patch.start()
        self.addCleanup(patch.stop)
        self.claim()

    def kubectl(self, argv, timeout=30, parsed=False):
        self.calls.append(argv)
        supervisor = argv[argv.index("--context") + 1] == "super"
        if "/apis/cluster.x-k8s.io" in argv:
            return reply(supervisor, "{}" if supervisor else "Error from server (NotFound): the server could not find the requested resource")
        for words, answer in (self.sup if supervisor else self.work).items():
            if all(w in argv for w in words.split()):
                return reply(True, answer if isinstance(answer, str) else json.dumps(answer))
        return reply(False, "Error from server (NotFound): not found")

    def claim(self, phase="Bound", sup_phase="Bound", attached=True, error=""):
        meta = {"name": "data", "namespace": "shop", "uid": self.UID}
        self.work["persistentvolumeclaims data"] = {"metadata": meta, "status": {"phase": phase}, "spec": {
            "volumeName": "pvc-9" if phase == "Bound" else None, "storageClassName": "gold", "resources": {"requests": {"storage": "20Gi"}}}}
        self.work["persistentvolumes pvc-9"] = {"spec": {"csi": {"volumeHandle": self.HANDLE}}, "status": {"phase": "Bound"}}
        self.work["volumeattachments"] = {"items": [{"spec": {"nodeName": "w-1", "source": {"persistentVolumeName": "pvc-9"}},
                                                     "status": {"attached": attached, **({"attachError": {"message": error}} if error else {})}}]}
        self.work["pods"] = {"items": [{**pod("db-0", ready=attached, node="w-1"), "spec": {
            "nodeName": "w-1", "volumes": [{"persistentVolumeClaim": {"claimName": "data"}}]}}]}
        self.work["events"] = "LAST SEEN TYPE REASON\n1m Warning ProvisioningFailed storage policy quota exceeded"
        self.sup["persistentvolumeclaims"] = {"items": [
            {"metadata": {"name": self.HANDLE, "namespace": "team-a"}, "status": {"phase": sup_phase}},
            {"metadata": {"name": "someone-elses", "namespace": "team-a"}, "status": {"phase": "Pending"}}]}
        self.sup["cnsnodevmattachments.cns.vmware.com"] = {"items": [
            {"metadata": {"name": "w-1-" + self.HANDLE, "namespace": "team-a"}, "spec": {"volumeName": self.HANDLE},
             "status": {"attached": attached, **({"error": "disk is locked by another VM"} if not attached else {})}}]}
        self.sup["resourcequotas"] = {"items": [{"status": {
            "hard": {"gold.storageclass.storage.k8s.io/requests.storage": "100Gi", "requests.cpu": "10"},
            "used": {"gold.storageclass.storage.k8s.io/requests.storage": "95Gi", "requests.cpu": "10"}}}]}
        self.sup["events"] = "LAST SEEN TYPE REASON\n1m Warning ProvisioningFailed datastore is out of space"

    def test_healthy_volume_shows_the_whole_chain(self):
        out = self.box.vks_volume_check("work", "data", "shop")
        for line in ("CLAIM shop/data (context work): Bound, 20Gi, storage class gold",
                     f"Volume pvc-9: Bound, known to VMware as {self.HANDLE}",
                     "Attached to node w-1", "Used by pod db-0 on node w-1",
                     f"SUPERVISOR context super, namespace team-a: claim {self.HANDLE} is Bound",
                     f"Attached to VM, by w-1-{self.HANDLE}"):
            self.assertIn(line, out)
        self.assertNotIn("someone-elses", out)

    def test_attach_failure_shows_both_sides(self):
        self.claim(attached=False, error="rpc error: failed to attach disk")
        out = self.box.vks_volume_check("work", "data", "shop")
        self.assertIn("- NOT attached to node w-1: rpc error: failed to attach disk", out)
        self.assertIn(f"- NOT attached to VM, by w-1-{self.HANDLE}: disk is locked by another VM", out)
        self.assertIn("- pod db-0 on node w-1: CrashLoopBackOff", out)

    def test_pending_claim_is_found_in_the_supervisor_by_its_id(self):
        self.claim(phase="Pending", sup_phase="Pending")
        out = self.box.vks_volume_check("work", "data", "shop")
        self.assertIn("- the claim is Pending: no volume has been made for it yet", out)
        self.assertIn("storage policy quota exceeded", out)
        self.assertIn("- VMware has not provided the volume (Pending)", out)
        self.assertIn("datastore is out of space", out)
        self.assertIn("- storage limit gold.storageclass.storage.k8s.io/requests.storage in namespace team-a: 95Gi used of 100Gi", out)
        self.assertNotIn("requests.cpu", out)

    def test_without_a_supervisor_login(self):
        box = tools.Toolbox(Settings(), clusters={"work": tools.Cluster("work", "", "work")})
        out = box.vks_volume_check(None, "data", "shop")
        self.assertIn("CLAIM shop/data", out)
        self.assertIn("no Supervisor context found that holds this cluster", out)

    def test_overview_lists_only_problems(self):
        self.claim(attached=False)
        self.work["persistentvolumeclaims"] = {"items": [
            {"metadata": {"name": "data", "namespace": "shop"}, "status": {"phase": "Bound"}},
            {"metadata": {"name": "cache", "namespace": "shop"}, "status": {"phase": "Pending"}}]}
        out = self.box.vks_volume_check("work", None, None)
        self.assertIn("CONTEXT work: 2 claims; 2 volume problems", out)
        self.assertIn("- claim shop/cache: Pending", out)
        self.assertIn("- volume pvc-9 not attached to node w-1", out)
        self.assertIn("- claim team-a/someone-elses: Pending", out)
        self.assertIn(f"- team-a/w-1-{self.HANDLE} not attached to its VM: disk is locked by another VM", out)
        self.assertNotIn("shop/data:", out)

    def test_a_claim_in_the_vsphere_namespace_is_read_there(self):
        """The same name can live in the vSphere namespace and inside a workload cluster: they are not the same."""
        self.sup["persistentvolumeclaims shopdb -n team-a"] = {
            "metadata": {"name": "shopdb", "namespace": "team-a", "creationTimestamp": stamp(98 * 86400)},
            "spec": {"volumeName": "shopdb-pv", "storageClassName": "vks-sp"}, "status": {"phase": "Pending"}}
        self.sup["persistentvolumes shopdb-pv"] = "Error from server (Forbidden): persistentvolumes is forbidden"
        self.sup["pods -n team-a"] = {"items": []}
        self.sup["virtualmachines.vmoperator.vmware.com -n team-a"] = {"items": []}
        self.sup["events"] = "LAST SEEN TYPE REASON"
        first = ("persistentvolumeclaims shopdb -n team-a", "pods -n team-a", "virtualmachines.vmoperator.vmware.com -n team-a")
        self.sup = {**{k: self.sup[k] for k in first}, **{k: v for k, v in self.sup.items() if k not in first}}
        real = self.kubectl

        def forbid(argv, timeout=30, parsed=False):
            if "persistentvolumes" in argv and "shopdb-pv" in argv:
                return reply(False, "Error from server (Forbidden): persistentvolumes is forbidden")
            return real(argv, timeout, parsed)
        with mock.patch.object(tools, "run", forbid):
            out = self.box.vks_volume_check("super", "shopdb", None)
        for line in ("SUPERVISOR CLAIM team-a/shopdb (context super: a claim in the vSphere namespace itself",
                     "Pending", "storage class vks-sp", "made 98 days ago", "Used by: nothing",
                     "- It asks for the volume shopdb-pv by name", "can't read volumes on the Supervisor",
                     "No events for it", "most likely left over"):
            self.assertIn(line, out)

    def test_with_several_supervisors_the_one_holding_the_cluster_is_used(self):
        self.box = tools.Toolbox(Settings(), clusters={
            "work": tools.Cluster("work", "", "work", api="https://10.9.9.9:6443"),
            "super": tools.Cluster("super", "", "super", namespace="team-a", api="https://sup"),
            "other": tools.Cluster("other", "", "other", namespace="team-b", api="https://sup")})
        real = self.kubectl

        def two(argv, timeout=30, parsed=False):
            ctx = argv[argv.index("--context") + 1]
            if "/apis/cluster.x-k8s.io" in argv:
                return reply(ctx != "work", "{}" if ctx != "work" else "Error from server (NotFound): not found")
            if "clusters.cluster.x-k8s.io" in argv:
                host = "10.9.9.9" if ctx == "super" else "10.1.1.1"
                return reply(True, json.dumps({"items": [{"metadata": {"name": "c-" + ctx, "namespace": "x"},
                                                          "spec": {"controlPlaneEndpoint": {"host": host}}}]}))
            return real(argv if ctx != "other" else argv, timeout, parsed)
        with mock.patch.object(tools, "run", two):
            self.assertEqual(self.box._find_supervisor("work"), "super")
            out = self.box.vks_volume_check("work", "data", "shop")
        self.assertIn(f"SUPERVISOR context super, namespace team-a: claim {self.HANDLE} is Bound", out)

    def test_a_bad_claim_name_is_refused(self):
        with self.assertRaises(tools.BadInput):
            self.box.vks_volume_check("work", "a;b", "shop")

    def test_quantities(self):
        self.assertEqual(tools.amount("20Gi"), 20 * 2 ** 30)
        self.assertEqual(tools.amount("1500M"), 1.5e9)
        self.assertEqual(tools.amount("10"), 10)
        self.assertIsNone(tools.amount("lots"))

    def test_host_log_units_still_there(self):
        self.assertIn("docker.service", tools.UNITS["docker"])


def snapshot(when, image="shop/web:1.2", ready=3, restarts=None, config="100", machine="Running", docker=None):
    return {"time": when, "docker": docker if docker is not None else {
                "relay": {"image": "relay:4", "build": "abc", "state": "running", "restarts": 0, "started": "2026-11-01T00:00:00Z"}},
            "k8s": {"a": {
                "workloads": {"deployment shop/web": {"images": [image], "replicas": 3, "ready": ready, "version": 4}},
                "nodes": {"n1": {"ready": "True", "kubelet": "v1.33.1"}},
                "restarts": restarts or {},
                "configmaps": {"shop/web-config": config},
                "vks": {"machine team-a/prod1-w-2 of cluster prod1": machine}}}}


class History(unittest.TestCase):
    def setUp(self):
        self.s = Settings(log_dir=tempfile.mkdtemp())

    def test_names_each_difference(self):
        then = snapshot(1000)
        now = snapshot(5000, image="shop/web:1.3", ready=1, restarts={"shop/web-7": 6}, config="250", machine="Provisioning")
        now["docker"]["relay"].update(image="relay:5", started="2026-11-15T03:00:00Z")
        now["docker"]["extra"] = {"image": "tool:1", "state": "running"}
        out = "\n".join(history.differences(then, now))
        for line in ("- container relay: image relay:4 -> relay:5; started again at 2026-11-15 03:00 UTC",
                     "- new container extra: image tool:1, running",
                     "- deployment shop/web: image shop/web:1.2 -> shop/web:1.3; ready 3 -> 1",
                     "- new pod shop/web-7: restarted 6 times",
                     "- configmap shop/web-config: its content changed",
                     "- VKS machine team-a/prod1-w-2 of cluster prod1: Running -> Provisioning"):
            self.assertIn(line, out)
        self.assertNotIn("node", out)

    def test_nothing_changed_is_empty(self):
        self.assertEqual(history.differences(snapshot(1000), snapshot(5000)), [])

    def test_unreadable_part_is_not_a_change(self):
        now = snapshot(5000)
        now["k8s"]["a"], now["docker"] = None, None
        self.assertEqual(history.differences(snapshot(1000), now), [])

    def test_saved_only_when_different_and_login_loss_keeps_last_state(self):
        self.assertTrue(history.save(self.s, snapshot(1000)))
        self.assertFalse(history.save(self.s, snapshot(1300)))
        lost = snapshot(1600)
        lost["k8s"]["a"] = None
        self.assertFalse(history.save(self.s, lost))
        self.assertTrue(history.save(self.s, snapshot(1900, image="shop/web:1.3")))
        self.assertEqual(len(os.listdir(history.folder(self.s))), 2)

    def test_picks_the_state_from_before_the_period(self):
        history.save(self.s, snapshot(1000))
        history.save(self.s, snapshot(2000, image="shop/web:1.3"))
        history.save(self.s, snapshot(3000, image="shop/web:1.4"))
        got, _ = history.before(self.s, 2500)
        self.assertEqual(got["k8s"]["a"]["workloads"]["deployment shop/web"]["images"], ["shop/web:1.3"])
        self.assertEqual(history.before(self.s, 500), (None, None))

    def test_old_snapshots_are_removed(self):
        history.save(self.s, snapshot(1000))
        first = os.path.join(history.folder(self.s), os.listdir(history.folder(self.s))[0])
        os.utime(first, (0, 0))
        history.save(self.s, snapshot(2000, image="shop/web:1.3"))
        self.assertEqual(len(os.listdir(history.folder(self.s))), 1)

    def test_each_host_keeps_its_own_snapshots_in_the_shared_folder(self):
        shared = os.path.join(self.s.log_dir, "snapshots")
        os.makedirs(shared)
        with open(os.path.join(shared, "19700101-000500.json"), "w") as f:   # from before: every host's, mixed
            json.dump(snapshot(300, image="shop/web:0.9"), f)
        for host, image in (("host-01", "shop/web:1.1"), ("host-02", "shop/web:2.2")):
            with mock.patch.object(config.socket, "gethostname", lambda h=host: h):
                history.save(self.s, {**snapshot(1000, image=image), "host": host})
        self.assertEqual(sorted(os.listdir(shared)), ["host-01", "host-02"])   # the mixed one is gone
        with mock.patch.object(config.socket, "gethostname", lambda: "host-01"):
            got, _ = history.before(self.s, 2000)
            self.assertEqual(got["k8s"]["a"]["workloads"]["deployment shop/web"]["images"], ["shop/web:1.1"])
            self.assertEqual(got["host"], "host-01")
            mine = os.path.join(shared, "host-01", os.listdir(os.path.join(shared, "host-01"))[0])
        with gzip.open(mine, "wt") as f:   # another host's snapshot in this one's folder is never used
            json.dump({**snapshot(1000), "host": "host-02"}, f)
        with mock.patch.object(config.socket, "gethostname", lambda: "host-01"):
            self.assertEqual(history.before(self.s, 2000), (None, None))

    def test_folder_never_passes_its_size_limit(self):
        with mock.patch.object(history, "MOST_BYTES", 2500):
            for n in range(12):
                history.save(self.s, snapshot(1000 + n * 300, image=f"shop/web:1.{n}", docker={"pad": {"image": os.urandom(300).hex()}}))
        names = sorted(os.listdir(history.folder(self.s)))
        size = sum(os.path.getsize(os.path.join(history.folder(self.s), n)) for n in names)
        self.assertLess(len(names), 12)
        self.assertLessEqual(size, 2500 + 1000)  # the newest file is always kept, so one file over at most
        self.assertTrue(names[-1].startswith(time.strftime("%Y%m%d-%H%M%S", time.gmtime(1000 + 11 * 300))))

    def test_files_are_compressed_and_old_plain_ones_still_read(self):
        history.save(self.s, snapshot(1000))
        name = os.listdir(history.folder(self.s))[0]
        self.assertTrue(name.endswith(".json.gz"))
        with open(os.path.join(history.folder(self.s), name), "rb") as f:
            self.assertEqual(f.read(2), b"\x1f\x8b")
        with open(os.path.join(history.folder(self.s), "19700101-000500.json"), "w") as f:
            json.dump(snapshot(300, image="shop/web:1.0"), f)
        got, _ = history.before(self.s, 500)
        self.assertEqual(got["k8s"]["a"]["workloads"]["deployment shop/web"]["images"], ["shop/web:1.0"])

    def test_snapshot_holds_no_settings_or_secret_values(self):
        inspect = [{"Name": "/relay", "Image": "sha256:" + "a" * 64, "RestartCount": 2,
                    "Config": {"Image": "relay:4", "Env": ["DB_PASSWORD=hunter2"], "Labels": {}},
                    "State": {"Status": "running", "StartedAt": "2026-11-01T00:00:00Z"}}]
        with mock.patch.object(history, "run", lambda argv, **k: reply(True, "id1" if "ps" in argv else json.dumps(inspect))):
            got = history._docker()
        self.assertEqual(got["relay"]["restarts"], 2)
        self.assertNotIn("hunter2", json.dumps(got))

    def test_timeline_from_the_cluster(self):
        def rs(name, rev, image, made):
            return {"metadata": {"name": name, "namespace": "shop", "creationTimestamp": made,
                                 "annotations": {"deployment.kubernetes.io/revision": str(rev)},
                                 "ownerReferences": [{"kind": "Deployment", "name": "web"}]},
                    "spec": {"template": {"spec": {"containers": [{"image": image}]}}}}
        items = {"replicasets.apps": [rs("web-a", 4, "shop/web:1.2", "2026-11-10T00:00:00Z"), rs("web-b", 5, "shop/web:1.3", "2026-11-15T03:02:00Z")],
                 "pods": [pod("web-7", restarts=6, ended="2026-11-15T03:05:10Z"), pod("web-8", ended="2026-11-01T00:00:00Z")],
                 "nodes": [{"metadata": {"name": "n1"}, "status": {"conditions": [
                     {"type": "Ready", "status": "False", "reason": "KubeletNotReady", "lastTransitionTime": "2026-11-15T03:20:00Z"}]}}]}
        with mock.patch.object(history, "_items", lambda box, cid, kind: items[kind]):
            rows = history.timeline(None, "a", history.stamp("2026-11-15T03:00:00Z"))
        self.assertEqual(rows, [
            "- 03:02 UTC deployment shop/web rolled out revision 5: image shop/web:1.2 -> shop/web:1.3",
            "- 03:05 UTC pod shop/web-7 container app ended (OOMKilled, exit code 137) and was restarted; 6 restarts in all",
            "- 03:20 UTC node n1: Ready became False (KubeletNotReady)"])

    def test_tool_says_when_there_is_nothing_to_compare(self):
        box = tools.Toolbox(self.s, clusters={})
        with mock.patch.object(history, "take", lambda b: {"time": 5000, "docker": {}, "k8s": {}}):
            self.assertIn("Nothing was recorded before now", box.what_changed("1h"))
            with self.assertRaises(tools.BadInput):
                box.what_changed("yesterday")


class Watch(unittest.TestCase):
    def test_each_problem_belongs_to_a_lane(self):
        agents = {"host-02": "host-02"}
        for k, want in (("host-01 DOCKER|relay|x", "host-01"), ("host-02 agent", "host-02"), ("KUBERNETES vks-a|pod|x", "vks-a"),
                        ("vks sup prod1|machine|x", "sup"), ("DOCKER|web|x", "host-01")):
            self.assertEqual(watch.lane(k, "host-01", agents), want)

    def test_map_lists_each_container_with_its_state(self):
        box = mock.Mock(_docker_rows=lambda: [
            {"Names": "web", "State": "running", "Status": "Up 2 hours", "Image": "web:1"},
            {"Names": "relay", "State": "running", "Status": "Up 1 minute (unhealthy)", "Image": "relay:5"},
            {"Names": "helper", "State": "running", "Status": "Up 1 second", "Image": "ts:1"},
            {"Names": "old", "State": "exited", "Status": "Exited (1) 2 days ago", "Image": "old:1"}],
            _meminfo=lambda: {"MemTotal": 1000, "MemAvailable": 250}, _disks=lambda: [(91, "/data: 91% used")])
        detail = {"web": {"restarts": 0}, "relay": {"restarts": 7}, "old": {}}
        with mock.patch.object(history, "_docker", lambda: detail):
            got = watch.local_map(box)
        self.assertEqual([(c["name"], c["state"]) for c in got["containers"]], [("old", "exited"), ("relay", "unhealthy"), ("web", "running")])
        self.assertEqual(got["containers"][1]["restarts"], 7)
        self.assertEqual(got["memory"], [750, 1000])
        self.assertEqual(got["disks"], [[91, "/data"]])

    def run_watch(self, docker_rounds, argv=(), kube=None, agents=None):
        box = mock.Mock(clusters={"vks": None} if kube else {}, roles={"vks": "workload"}, agents=agents or {}, here="host-01", s=Settings())
        box._disks.return_value = [(95, "/data: 95% used, 1.0 GB free of 20.0 GB"), (40, "/: 40% used")]
        box._meminfo.return_value = {"MemAvailable": 8000, "MemTotal": 16000}
        box._docker_summary.side_effect = docker_rounds
        box._swarm_summary.return_value = "SWARM: not active"
        box._k8s_summary.return_value = kube
        out, logged = io.StringIO(), []
        with redirect_stdout(out), mock.patch.object(history, "record", lambda b: None):
            code = watch.main(Settings(), box, list(argv), logged.append, sleep=lambda s: None, rounds=len(docker_rounds))
        return code, out.getvalue(), logged

    def test_speaks_only_on_change(self):
        bad = "DOCKER: 1 of 2 containers running; 1 need a look\n- relay: Exited (1) {} minutes ago"
        code, out, logged = self.run_watch([
            "DOCKER: 2 of 2 containers running; 0 need a look", bad.format(2), bad.format(9),
            "DOCKER: 2 of 2 containers running; 0 need a look"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("PROBLEM   host: disk /data: 95% used"), 1)
        self.assertEqual(out.count("NEW       docker: relay: Exited (1) 2 minutes ago"), 1)
        self.assertEqual(out.count("NEW "), 1)  # the same problem getting older is not news
        self.assertEqual(out.count("OK AGAIN  docker: relay"), 1)
        self.assertIn("Watch found a new problem: docker: relay: Exited (1) 2 minutes ago", logged)

    def test_unreachable_cluster_is_a_problem(self):
        _, out, _ = self.run_watch(["DOCKER: 1 of 1 containers running; 0 need a look"],
                                   kube="KUBERNETES vks: not reachable: the login was refused: the token has expired or is wrong.")
        self.assertIn("PROBLEM   kubernetes vks: not reachable: the login was refused", out)

    def test_a_failed_round_does_not_end_the_watch(self):
        _, out, _ = self.run_watch([RuntimeError("docker is down"), "DOCKER: 1 of 1 containers running; 0 need a look"])
        self.assertIn("This round of checks failed (docker is down)", out)

    def test_bad_arguments(self):
        self.assertEqual(self.run_watch([], argv=["--often"])[0], 2)

    def test_hub_names_the_host_of_each_problem(self):
        def ask(settings, host, tool, args=None, timeout=90):
            if host == "host-03":
                raise hosts.Unreachable("connection refused")
            return json.dumps({"problems": {"docker|relay exited": "docker: relay: Exited (1) 2 minutes ago"},
                               "lines": [{"kind": "Docker", "where": "", "line": "1 of 2 containers running", "problems": 1}]})
        with mock.patch.object(hosts, "ask", ask):
            _, out, _ = self.run_watch(["DOCKER: 1 of 1 containers running; 0 need a look"],
                                       agents={"host-02": "host-02", "host-03": "host-03"})
        self.assertIn("PROBLEM   host-01 host: disk /data", out)
        self.assertIn("PROBLEM   host-02 docker: relay: Exited (1) 2 minutes ago", out)
        self.assertIn("PROBLEM   host-03 its agent did not answer: connection refused", out)
        self.assertIn("and 2 other host(s)", out)


class Report(unittest.TestCase):
    def test_report_uses_the_conversation_and_is_saved(self):
        s = Settings(llm_url="http://x", llm_model="m", kube_dir="/none", log_dir=tempfile.mkdtemp())
        ui = UI()
        ui.spinner, ui.done = mock.Mock(), lambda: None
        sess = engine.Session(s, ui)
        self.assertIsNone(sess.report())
        self.assertIn("Nothing to write up yet", ui.notices[0])
        sess.messages += [{"role": "user", "content": "why is relay down?"}, call("docker_list"),
                          {"role": "tool", "tool_call_id": "1", "content": "relay Exited (1)"},
                          {"role": "assistant", "content": "It exited."}]
        seen = {}

        def fake(settings, messages, tools=None, **kw):
            seen["last"], seen["tools"] = messages[-1]["content"], tools
            return {"role": "assistant", "content": "SUMMARY\nrelay exited."}, "stop"
        out = io.StringIO()
        with mock.patch.object(llm, "chat", fake), redirect_stdout(out):
            cli.report(s, sess, ui)
        self.assertIn("incident report", seen["last"])
        self.assertIsNone(seen["tools"])
        name = os.listdir(os.path.join(s.log_dir, "reports"))[0]
        self.assertIn(f"Saved in the log folder: reports/{name}", out.getvalue())
        with open(os.path.join(s.log_dir, "reports", name)) as f:
            text = f.read()
        self.assertIn("SUMMARY\nrelay exited.", text)
        self.assertIn("Check it before passing it on", text)


NOTE = """TITLE: web pods crash after rollout: missing PAYMENT_URL
SYMPTOM
Pods of deployment shop/web in CrashLoopBackOff. Log: FATAL: missing required setting PAYMENT_URL
CAUSE
Image 1.3 needs PAYMENT_URL; the configmap did not have it. password=hunter2hunter2
FIX
Added PAYMENT_URL to configmap web-config and restarted. Confirmed.
CHECK
k8s_workload_logs on deployment web, match FATAL
WORDS: web shop CrashLoopBackOff PAYMENT_URL configmap rollout"""


class Cases(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp()
        self.s = Settings(llm_url="http://x", llm_model="m", kube_dir="/none",
                          log_dir=os.path.join(base, "logs"), runbook_dir=os.path.join(base, "runbooks"))
        os.makedirs(self.s.log_dir)
        os.makedirs(self.s.runbook_dir)

    def share(self):
        """What ./ts does on the host after a chat."""
        todo = cases.waiting(self.s)
        for key in todo["delete"]:
            host, name = key.split("/")
            os.unlink(os.path.join(self.s.runbook_dir, "_cases", host, name))
        for key, text in todo["save"].items():
            host, name = key.split("/")
            os.makedirs(os.path.join(self.s.runbook_dir, "_cases", host), exist_ok=True)
            with open(os.path.join(self.s.runbook_dir, "_cases", host, name), "w") as f:
                f.write(text)
        cases.sent(self.s, list(todo["save"]) + todo["delete"])

    def test_saved_case_is_found_by_its_words_and_masked(self):
        name = cases.save(self.s, NOTE, "work")
        self.assertRegex(name, r"^\d{8}-\d{6}-web-pods-crash-after-rollout-missing-payment-url\.md$")
        out = cases.search(self.s, "PAYMENT_URL crashloopbackoff")
        self.assertIn("Each is a lead, not proof", out)
        self.assertIn("# web pods crash after rollout: missing PAYMENT_URL", out)
        self.assertIn("context work", out)
        self.assertNotIn("hunter2", out)
        self.assertIn("None of the 1 saved cases", cases.search(self.s, "postgres"))
        self.assertIn("- web pods crash after rollout: missing PAYMENT_URL", cases.search(self.s, ""))

    def test_best_match_first_and_only_a_few_in_full(self):
        for n in range(5):
            cases.save(self.s, f"TITLE: disk full on node {n}\nSYMPTOM\nno space left on device\nWORDS: disk node", "")
            os.rename(os.path.join(self.s.log_dir, "cases", os.listdir(os.path.join(self.s.log_dir, "cases"))[0],
                                   sorted(os.listdir(os.path.join(self.s.log_dir, "cases", os.listdir(os.path.join(self.s.log_dir, "cases"))[0])))[-1]),
                      os.path.join(self.s.log_dir, "cases", os.listdir(os.path.join(self.s.log_dir, "cases"))[0], f"2026010{n}-000000-disk-{n}.md"))
        cases.save(self.s, NOTE, "")
        out = cases.search(self.s, "PAYMENT_URL disk")
        self.assertEqual(out.count("--- case from host"), 3)
        self.assertIn("Also matching: ", out)
        self.assertLess(out.index("disk full on node 4"), out.index("disk full on node 3"))

    def test_copied_to_the_shared_folder_once_and_then_read_from_there(self):
        name = cases.save(self.s, NOTE, "")
        host = os.listdir(os.path.join(self.s.log_dir, "cases"))[0]
        self.assertEqual(list(cases.waiting(self.s)["save"]), [f"{host}/{name}"])
        self.share()
        self.assertEqual(cases.waiting(self.s), {"save": {}, "delete": []})
        self.assertEqual(os.listdir(os.path.join(self.s.log_dir, "cases", host)), [])
        self.assertEqual(len(cases.all_cases(self.s)), 1)
        os.unlink(os.path.join(self.s.runbook_dir, "_cases", host, name))  # the operator deletes a case
        self.assertEqual(cases.all_cases(self.s), [])                      # ... and it does not come back

    def test_local_copy_is_kept_until_the_shared_one_exists(self):
        name = cases.save(self.s, NOTE, "")
        host = os.listdir(os.path.join(self.s.log_dir, "cases"))[0]
        cases.sent(self.s, [f"{host}/{name}", "../../etc/passwd", "x"])
        self.assertEqual(len(cases.all_cases(self.s)), 1)

    def test_edit_and_delete_from_the_page_reach_the_shared_folder(self):
        name = cases.save(self.s, NOTE, "")
        host = os.listdir(os.path.join(self.s.log_dir, "cases"))[0]
        key = f"{host}/{name}"
        self.share()
        shared = os.path.join(self.s.runbook_dir, "_cases", host, name)
        cases.rewrite(self.s, key, "TITLE: corrected\nCAUSE\nthe real one, token=abcdefgh12345678")
        self.assertIn("corrected", cases.find(self.s, key))        # at once on this host
        self.assertNotIn("abcdefgh12345678", cases.find(self.s, key))
        with open(shared) as f:
            self.assertIn("PAYMENT_URL", f.read())                  # the shared copy waits for ./ts
        self.share()
        with open(shared) as f:
            self.assertIn("corrected", f.read())
        self.assertEqual(cases.waiting(self.s), {"save": {}, "delete": []})
        cases.remove(self.s, key)
        self.assertEqual(cases.all_cases(self.s), [])
        self.assertTrue(os.path.exists(shared))
        self.share()
        self.assertFalse(os.path.exists(shared))
        self.assertEqual(cases.waiting(self.s), {"save": {}, "delete": []})
        for bad in ("../x/y.md", "host/../../etc.md", "nohost"):
            with self.assertRaises(ValueError):
                cases.remove(self.s, bad)

    def test_a_case_not_yet_shared_is_edited_and_deleted_in_place(self):
        name = cases.save(self.s, NOTE, "")
        key = f"{os.listdir(os.path.join(self.s.log_dir, 'cases'))[0]}/{name}"
        cases.rewrite(self.s, key, "TITLE: corrected")
        self.assertEqual(cases.find(self.s, key).strip(), "TITLE: corrected")
        cases.remove(self.s, key)
        self.assertEqual(cases.waiting(self.s), {"save": {}, "delete": []})

    def test_cases_are_not_runbooks_and_other_files_are_ignored(self):
        cases.save(self.s, NOTE, "")
        self.share()
        folder = os.path.join(self.s.runbook_dir, "_cases", os.listdir(os.path.join(self.s.runbook_dir, "_cases"))[0])
        for bad in (".hidden.md", "notes.txt"):
            with open(os.path.join(folder, bad), "w") as f:
                f.write("x")
        box = tools.Toolbox(self.s, clusters={})
        self.assertEqual(box._runbooks(), [])
        self.assertEqual(len(cases.all_cases(self.s)), 1)
        self.assertIn("PAYMENT_URL", box.tools["past_cases"].fn(words="payment_url"))

    def test_chat_is_told_about_cases_only_when_there_are_some(self):
        sess = engine.Session(self.s, UI())
        self.assertNotIn("past_cases", sess.messages[0]["content"])
        self.assertIn("/case", sess.messages[0]["content"])
        cases.save(self.s, NOTE, "")
        sess.reset()
        self.assertIn("1 past cases are saved", sess.messages[0]["content"])

    def run_case(self, answer):
        ui = UI()
        ui.spinner, ui.done = mock.Mock(), lambda: None
        sess = engine.Session(self.s, ui)
        sess.messages += [{"role": "user", "content": "why is web down?"}, call("docker_list"),
                          {"role": "tool", "tool_call_id": "1", "content": "x"}, {"role": "assistant", "content": "y"}]
        out, logged = io.StringIO(), []
        with mock.patch.object(llm, "chat", lambda *a, **k: ({"role": "assistant", "content": NOTE}, "stop")), \
                mock.patch("builtins.input", lambda prompt: answer), redirect_stdout(out):
            cli.case(self.s, sess, ui, logged.append)
        return out.getvalue(), logged

    def test_case_command_saves_only_on_yes(self):
        out, logged = self.run_case("no")
        self.assertIn("Not saved.", out)
        self.assertEqual(cases.all_cases(self.s), [])
        out, logged = self.run_case("yes")
        self.assertIn("Saved: web pods crash after rollout: missing PAYMENT_URL", out)
        self.assertEqual(len(cases.all_cases(self.s)), 1)
        self.assertTrue(logged[0].startswith("You saved a case: "))

    def test_launcher_writes_cases_to_the_shared_folder(self):
        home, calls = tempfile.mkdtemp(), []
        found = {"save": {"host-a/20261115-000000-web.md": "# web\ntext", "../x/evil.md": "bad", "host-a/.hid.md": "bad",
                          "host-a/a b.md": "bad", "host-a/notes.txt": "bad"},
                 "delete": ["host-a/20260101-000000-old.md", "../../etc/passwd"]}
        os.makedirs(os.path.join(home, "runbooks", "_cases", "host-a"))
        with open(os.path.join(home, "runbooks", "_cases", "host-a", "20260101-000000-old.md"), "w") as f:
            f.write("old")

        def quiet(argv):
            calls.append(argv)
            return "" if "--sent" in argv else json.dumps(found)
        out, here = io.StringIO(), os.getcwd()
        os.chdir(home)
        try:
            with mock.patch.object(launcher, "quiet", quiet), redirect_stdout(out):
                launcher.share_cases()
        finally:
            os.chdir(here)
        self.assertEqual(os.listdir(os.path.join(home, "runbooks", "_cases")), ["host-a"])
        self.assertEqual(os.listdir(os.path.join(home, "runbooks", "_cases", "host-a")), ["20261115-000000-web.md"])
        self.assertEqual(calls[-1][-3:], ["--sent", "host-a/20260101-000000-old.md", "host-a/20261115-000000-web.md"])
        self.assertIn("Updated 2 case(s) for every host", out.getvalue())
        self.assertEqual(os.stat(os.path.join(home, "runbooks", "_cases", "host-a", "20261115-000000-web.md")).st_mode & 0o777, 0o644)


class LearnModel(unittest.TestCase):
    CHAT = dict(llm_url="https://gpt.example/v1", llm_model="gpt-5.4", llm_api_key="chat-key")

    def test_same_as_chat_when_not_set(self):
        s = Settings(**self.CHAT)
        self.assertIs(config.for_learning(s), s)

    def test_other_server_never_gets_the_chat_key(self):
        got = config.for_learning(Settings(**self.CHAT, learn_llm_url="http://vllm.example/v1", learn_llm_model="nemotron"))
        self.assertEqual((got.llm_url, got.llm_model, got.llm_api_key), ("http://vllm.example/v1", "nemotron", ""))
        got = config.for_learning(Settings(**self.CHAT, learn_llm_url="http://vllm.example/v1", learn_llm_model="nemotron",
                                           learn_llm_api_key="learn-key"))
        self.assertEqual(got.llm_api_key, "learn-key")

    def test_other_model_on_the_same_server_keeps_the_key(self):
        got = config.for_learning(Settings(**self.CHAT, learn_llm_model="gpt-5.4-mini"))
        self.assertEqual((got.llm_url, got.llm_model, got.llm_api_key), ("https://gpt.example/v1", "gpt-5.4-mini", "chat-key"))

    def test_learn_talks_to_the_learn_model(self):
        seen = {}

        def fake(settings, argv, log):
            seen["model"], seen["reasoning"] = settings.llm_model, settings.llm_reasoning
            return 0
        with mock.patch.object(learn, "_learn", fake):
            learn.main(Settings(**self.CHAT, learn_llm_model="nemotron"), ["/apps/x"], lambda line: None)
        self.assertEqual(seen, {"model": "nemotron", "reasoning": "off"})


class SupervisorIsNotChanged(unittest.TestCase):
    def test_fix_tools_refuse_a_supervisor(self):
        box = tools.Toolbox(Settings(), clusters={"work": tools.Cluster("work", "", "work"), "super": tools.Cluster("super", "", "super")})
        box.roles = {"work": "workload", "super": "supervisor"}
        for change in (lambda c: box.k8s_delete_pod(c, "web-1", "shop"), lambda c: box.k8s_restart(c, "deployments", "web", "shop"),
                       lambda c: box.k8s_scale(c, "deployments", "web", "shop", 3)):
            self.assertIsInstance(change("work"), tools.Change)
            with self.assertRaises(tools.BadInput) as e:
                change("super")
            self.assertIn("nothing there is changed from here", str(e.exception))

    def test_refusal_reaches_the_model_and_nothing_is_asked(self):
        ui = UI(approve=True)
        sess = engine.Session(Settings(llm_url="http://x", llm_model="m", kube_dir="/none"), ui,
                              toolbox=tools.Toolbox(Settings(), clusters={"super": tools.Cluster("super", "", "super")}))
        sess.box.roles = {"super": "supervisor"}
        text = sess._run_one(call("k8s_restart", **{"kind": "deployments", "name": "web", "namespace": "shop", "reason": "it is stuck"})["tool_calls"][0], {})
        self.assertIn("Refused: super is a VMware Supervisor", text)
        self.assertEqual(ui.asked, [])


class Recorder(unittest.TestCase):
    def test_records_on_its_timer_and_can_be_turned_off(self):
        taken, slept, out = [], [], io.StringIO()
        with mock.patch.object(history, "record", taken.append), redirect_stdout(out):
            history.record_forever("box", 5, sleep=slept.append, rounds=3)
            history.record_forever("box", 0, sleep=slept.append, rounds=3)
        self.assertEqual((taken, slept), (["box"] * 3, [300] * 3))
        self.assertIn("Background recording is off", out.getvalue())

    def test_snapshot_without_a_login_keeps_the_clusters_last_state(self):
        s = Settings(log_dir=tempfile.mkdtemp())
        history.save(s, snapshot(1000))
        no_login = {"time": 1300, "docker": snapshot(1300)["docker"], "k8s": {}}
        self.assertFalse(history.save(s, no_login))            # not a change: nothing new was seen
        self.assertFalse(history.save(s, snapshot(1600)))      # the chat comes back: still the same
        self.assertEqual(len(os.listdir(history.folder(s))), 1)
        got, _ = history.before(s, 2000)
        self.assertIn("a", got["k8s"])


def on(host, **more):
    """Settings as seen on the host named `host`."""
    return mock.patch.object(config.socket, "gethostname", lambda: host), Settings(**more)


class Roles(unittest.TestCase):
    def test_one_setting_decides_what_each_host_is(self):
        for host, want in (("host-01", "hub"), ("HOST-01.example.com", "hub"), ("host-02", "agent")):
            patch, s = on(host, hub_host="host-01", agent_hosts=("host-01", "host-02", "host-03=10.0.0.13"))
            with patch:
                self.assertEqual(config.role(s), want)
                self.assertEqual(config.agents(s), {"host-02": "host-02", "host-03": "10.0.0.13"} if want == "hub" else {})
        patch, s = on("host-01")
        with patch:
            self.assertEqual(config.role(s), "alone")

    def test_only_the_hub_gets_a_host_choice_and_never_on_fix_or_cluster_tools(self):
        patch, s = on("host-01", hub_host="host-01", agent_hosts=("host-02",))
        with patch:
            box = tools.Toolbox(s, clusters=ONE)
        self.assertEqual(box.tools["docker_logs"].params["host"].enum, ("host-01", "host-02"))
        for name in ("docker_restart", "k8s_get", "vks_cluster_health", "past_cases", "k8s_contexts"):
            self.assertNotIn("host", box.tools[name].params)
        self.assertEqual(box.tools["read_runbook"].params["host"].enum, ())  # its own argument: whose runbook
        patch, s = on("host-02", hub_host="host-01", agent_hosts=("host-02",))
        with patch:
            self.assertNotIn("host", tools.Toolbox(s, clusters={}).tools["docker_logs"].params)

    def test_a_tool_goes_to_the_other_host_only_when_asked(self):
        patch, s = on("host-01", hub_host="host-01", agent_hosts=("host-02",))
        asked = []

        def ask(settings, host, tool, args=None, timeout=90):
            asked.append((host, tool, args))
            if host == "host-02":
                return "from host-02"
            raise hosts.Unreachable("no answer")
        with patch, mock.patch.object(hosts, "ask", ask), mock.patch.object(tools, "run", lambda *a, **k: reply(True, "from here")):
            box = tools.Toolbox(s, clusters={})
            t = box.tools["docker_logs"]
            self.assertEqual(t.fn(**t.check({"container": "web", "host": "host-02", "tail": 5})), "from host-02")
            self.assertEqual(asked, [("host-02", "docker_logs", {"container": "web", "tail": 5})])
            self.assertEqual(t.fn(**t.check({"container": "web"})), "from here")
            self.assertEqual(t.fn(**t.check({"container": "web", "host": "host-01"})), "from here")
            self.assertEqual(len(asked), 1)
            self.assertEqual(t.describe({"container": "web", "host": "host-02"}), "Read logs of container web on host-02")
            with self.assertRaises(tools.BadInput):
                t.check({"container": "web", "host": "host-99"})

    def test_what_changed_on_the_hub_looks_at_every_host_each_under_its_name(self):
        patch, s = on("host-01", hub_host="host-01", agent_hosts=("host-02", "host-03"))
        asked = []

        def ask(settings, host, tool, args=None, timeout=90):
            asked.append((host, tool))
            if host == "host-02":
                return "- new container relay"
            raise hosts.Unreachable("no answer")
        with patch, mock.patch.object(hosts, "ask", ask), mock.patch.object(history, "what_changed", lambda box, since: "- new container web"):
            box = tools.Toolbox(s, clusters={})
            t = box.tools["what_changed"]
            out = t.fn(**t.check({"since": "6h"}))
            self.assertEqual(sorted(asked), [("host-02", "what_changed"), ("host-03", "what_changed")])
            self.assertLess(out.index("=== HOST host-01"), out.index("- new container web"))
            self.assertLess(out.index("- new container web"), out.index("=== HOST host-02"))
            self.assertLess(out.index("=== HOST host-02"), out.index("- new container relay"))
            self.assertIn("Could not look at host-03: no answer", out)
            asked.clear()
            self.assertEqual(t.fn(**t.check({"since": "6h", "host": "host-01"})), "- new container web")
            self.assertEqual(t.fn(**t.check({"since": "6h", "host": "host-02"})), "- new container relay")
            self.assertEqual(asked, [("host-02", "what_changed")])

    def test_model_is_told_about_the_other_hosts(self):
        patch, s = on("host-01", hub_host="host-01", agent_hosts=("host-02",), llm_url="http://x", llm_model="m", kube_dir="/none")
        with patch:
            sess = engine.Session(s, UI())
        self.assertIn("You are on the hub host host-01", sess.messages[0]["content"])
        self.assertIn("You can only change things on host-01", sess.messages[0]["content"])


class AgentAnswers(unittest.TestCase):
    def setUp(self):
        patch, s = on("host-02", hub_host="host-01=10.0.0.11", agent_key="k", log_dir=tempfile.mkdtemp())
        with patch:
            self.logged = []
            self.agent = hosts.Agent(s, self.logged.append)

    def test_looks_only_and_only_at_its_own_machine(self):
        for tool in ("docker_restart", "docker_stop", "k8s_get", "k8s_delete_pod", "vks_cluster_health", "past_cases", "read_runbook", "nonsense"):
            ok, text = self.agent.run(tool, {})
            self.assertFalse(ok)
            self.assertIn("not a tool this agent answers", text)

    def test_arguments_are_checked_as_in_a_chat(self):
        ok, text = self.agent.run("docker_inspect", {"container": "a;rm -rf /"})
        self.assertFalse(ok)
        self.assertIn("Refused", text)
        ok, text = self.agent.run("read_file", {"path": "/etc/shadow"})
        self.assertIn("Refused", text)
        self.assertTrue(any("Refused a read_file request from the hub" in line for line in self.logged))

    def test_runs_a_read_tool_and_logs_it(self):
        with mock.patch.object(tools, "run", lambda *a, **k: reply(True, "NAMES\nweb")):
            ok, text = self.agent.run("docker_list", {"include_stopped": False})
        self.assertTrue(ok)
        self.assertIn("web", text)
        self.assertIn("The hub asked: Listed containers", self.logged[-1])

    def test_only_the_hubs_address_may_call(self):
        self.assertTrue(self.agent.may_call("10.0.0.11"))
        self.assertTrue(self.agent.may_call("::ffff:10.0.0.11"))
        self.assertFalse(self.agent.may_call("10.0.0.99"))

    def test_not_ready_says_what_to_do(self):
        self.assertIn("./ts setup", hosts.not_ready(Settings()))
        self.assertIn("TLS_KEY", hosts.not_ready(Settings(agent_key="k")))
        self.assertIn("certificate", hosts.not_ready(Settings(agent_key="k", tls_key="x", tls_cert="/nowhere.pem")))

    def test_asking_an_unknown_host_is_refused_before_any_connection(self):
        patch, s = on("host-01", hub_host="host-01", agent_hosts=("host-02",))
        with patch, self.assertRaises(hosts.Unreachable) as e:
            hosts.ask(s, "host-99", "docker_list")
        self.assertIn("not one of the agent hosts", str(e.exception))


class DockerOnly(unittest.TestCase):
    def test_off_means_no_cluster_tools_and_no_word_of_clusters(self):
        with mock.patch.dict(os.environ, {"KUBE_PROXIES": "vks|7001|;", "KUBE_CURRENT": "vks"}):
            self.assertIn("k8s_get", tools.Toolbox(Settings(kube_dir="/none")).tools)
            s = Settings(kubernetes=False, kube_dir="/none", llm_url="http://x", llm_model="m")
            box = tools.Toolbox(s)
            sess = engine.Session(s, UI())
        self.assertEqual([n for n in box.tools if n.startswith(("k8s_", "vks_", "pod_"))], [])
        self.assertNotIn("Kubernetes", sess.messages[0]["content"])
        self.assertIn("docker_list", box.tools)

    def test_launcher_reads_the_setting_and_starts_no_proxies(self):
        home = tempfile.mkdtemp()
        for text, want in (('  kubernetes: &kubernetes "off"\n', True), ('  kubernetes: &kubernetes "on"\n', False),
                           ('  # kubernetes: &kubernetes "off"\n  kubernetes: &kubernetes "on"\n', False), ("", False)):
            with open(os.path.join(home, "compose.yaml"), "w") as f:
                f.write("x-settings:\n" + text)
            with mock.patch.object(launcher, "HOME", home):
                self.assertEqual(launcher.kubernetes_off(), want)


class Setup(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        with open(os.path.join(self.home, ".env"), "w") as f:
            f.write("# Secrets only.\nLLM_API_KEY=keep-me\n")
        patches = [mock.patch.object(launcher, "ENV", os.path.join(self.home, ".env")),
                   mock.patch.object(launcher, "CERT", os.path.join(self.home, "certs", "troubleshooter-cert.pem"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_it(self, fn, *args, passwords=()):
        given, out = list(passwords), io.StringIO()
        with mock.patch.object(launcher.getpass, "getpass", lambda prompt: given.pop(0)), redirect_stdout(out):
            code = fn(*args)
        return code, out.getvalue()

    def env(self):
        return launcher.env_read()[1]

    def test_setup_writes_keys_once_and_never_shows_them(self):
        code, out = self.run_it(launcher.setup, ["setup"])
        self.assertEqual(code, 0)
        env = self.env()
        self.assertEqual(env["LLM_API_KEY"], "keep-me")
        self.assertGreater(len(env["AGENT_KEY"]), 30)
        self.assertIn("PRIVATE KEY", __import__("base64").b64decode(env["TLS_KEY"]).decode())
        self.assertTrue(os.path.isfile(launcher.CERT))
        self.assertEqual(os.stat(launcher.ENV).st_mode & 0o777, 0o600)
        self.assertNotIn(env["AGENT_KEY"], out)
        self.assertNotIn(env["TLS_KEY"][:40], out)
        self.assertEqual(os.listdir(os.path.dirname(launcher.CERT)), ["troubleshooter-cert.pem"])
        code, out = self.run_it(launcher.setup, ["setup"])
        self.assertIn("Nothing to do", out)
        self.assertEqual(self.env()["AGENT_KEY"], env["AGENT_KEY"])

    def test_made_certificate_works_between_a_hub_and_an_agent(self):
        self.run_it(launcher.setup, ["setup"])
        env = self.env()
        s = Settings(agent_key=env["AGENT_KEY"], tls_key=env["TLS_KEY"], tls_cert=launcher.CERT)
        with mock.patch.object(hosts, "KEY_FILE", os.path.join(self.home, "key.pem")):
            self.assertEqual(hosts.not_ready(s), "")
            hosts.server_context(s)
            hosts.client_context(s)
            self.assertEqual(os.stat(os.path.join(self.home, "key.pem")).st_mode & 0o777, 0o600)

    def make(self, name, subject):
        key, cert = os.path.join(self.home, name + ".key"), os.path.join(self.home, name + ".pem")
        __import__("subprocess").run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "5", "-subj", subject,
                                      "-keyout", key, "-out", cert], check=True, capture_output=True)
        return cert, key

    def test_own_certificate_for_the_page_only(self):
        web_cert = os.path.join(self.home, "certs", "web-cert.pem")
        self.run_it(launcher.setup, ["setup"])
        cert, key = self.make("mine", "/CN=hub.example")
        other_cert, other_key = self.make("other", "/CN=other")
        with mock.patch.object(launcher, "WEB_CERT", web_cert), mock.patch.object(launcher, "START", self.home):
            for args, word in (([cert, other_key], "do not belong together"), ([key, key], "not a certificate"),
                               (["missing.pem", key], "Could not read"), ([cert], "Usage")):
                code, out = self.run_it(launcher.setup, ["setup", "--web-cert"] + args)
                self.assertNotEqual(code, 0)
                self.assertIn(word, out)
            self.assertNotIn("WEB_TLS_KEY", self.env())
            code, out = self.run_it(launcher.setup, ["setup", "--web-cert", "mine.pem", "mine.key"])  # names as typed
            self.assertEqual(code, 0)
            env = self.env()
            self.assertNotIn(env["WEB_TLS_KEY"][:40], out)
            s = Settings(agent_key=env["AGENT_KEY"], tls_key=env["TLS_KEY"], tls_cert=launcher.CERT,
                         web_tls_key=env["WEB_TLS_KEY"], web_cert=web_cert)
            self.assertTrue(hosts.own_web_cert(s))
            self.assertIn(hosts.days_left(web_cert), (4, 5))
            with mock.patch.object(hosts, "KEY_FILE", os.path.join(self.home, "k1")), \
                    mock.patch.object(hosts, "WEB_KEY_FILE", os.path.join(self.home, "k2")):
                hosts.server_context(s, web=True)
                self.assertTrue(os.path.exists(os.path.join(self.home, "k2")))
                self.assertFalse(os.path.exists(os.path.join(self.home, "k1")))   # the page did not touch the hosts' key
                hosts.server_context(s)                                            # agents: still the one setup made
                self.assertTrue(os.path.exists(os.path.join(self.home, "k1")))
            code, out = self.run_it(launcher.setup, ["setup", "--web-cert", "remove"])
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(web_cert))
            self.assertFalse(hosts.own_web_cert(Settings(web_tls_key=self.env()["WEB_TLS_KEY"], web_cert=web_cert)))

    def test_a_key_with_a_passphrase_is_refused_with_the_way_out(self):
        cert, key = self.make("mine", "/CN=hub.example")
        locked = os.path.join(self.home, "locked.key")
        __import__("subprocess").run(["openssl", "pkey", "-in", key, "-aes256", "-passout", "pass:secret123", "-out", locked],
                                      check=True, capture_output=True)
        with mock.patch.object(launcher, "START", self.home):
            code, out = self.run_it(launcher.setup, ["setup", "--web-cert", cert, locked])
        self.assertEqual(code, 1)
        self.assertIn("openssl pkey -in", out)

    def test_accounts_are_kept_as_hashes(self):
        code, out = self.run_it(launcher.user, ["user", "add", "alice"], passwords=["correct horse 1", "correct horse 1"])
        self.assertEqual(code, 0)
        stored = web.users(Settings(web_users=self.env()["WEB_USERS"]))
        self.assertNotIn("correct horse", self.env()["WEB_USERS"])
        self.assertTrue(web.password_ok(stored["alice"], "correct horse 1"))
        self.assertFalse(web.password_ok(stored["alice"], "correct horse 2"))
        self.assertFalse(web.password_ok("nonsense", "x"))
        self.run_it(launcher.user, ["user", "add", "bob"], passwords=["another long one", "another long one"])
        self.assertEqual(sorted(web.users(Settings(web_users=self.env()["WEB_USERS"]))), ["alice", "bob"])
        self.assertIn("alice\nbob", self.run_it(launcher.user, ["user", "list"])[1])
        self.run_it(launcher.user, ["user", "remove", "alice"])
        self.assertEqual(list(web.users(Settings(web_users=self.env()["WEB_USERS"]))), ["bob"])

    def test_bad_passwords_and_names_change_nothing(self):
        for args, passwords in ((["user", "add", "al"], ["short", "short"]), (["user", "add", "al"], ["long enough 1", "different 22"]),
                                (["user", "add", "a b"], []), (["user", "add", "a,b:c"], []), (["user", "remove", "ghost"], [])):
            code, _ = self.run_it(launcher.user, args, passwords=passwords)
            self.assertNotEqual(code, 0)
        self.assertNotIn("WEB_USERS", self.env())


class Page(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp()
        salt = "00" * 16
        import hashlib
        stored = "pbkdf2$1000$" + salt + "$" + hashlib.pbkdf2_hmac("sha256", b"right password", bytes.fromhex(salt), 1000).hex()
        self.s = Settings(llm_url="", llm_model="m", kube_dir="/none", log_dir=os.path.join(base, "logs"),
                          runbook_dir=os.path.join(base, "runbooks"), web_users="alice:" + stored + ",bob:" + stored)
        os.makedirs(self.s.log_dir)
        os.makedirs(os.path.join(self.s.runbook_dir, "host-01"))
        self.logged = []
        self.site = web.Site(self.s, self.logged.append)

    def test_a_hash_written_by_user_add_survives_docker_compose(self):
        """Compose reads "$ab12" in .env as a variable: ./ts user add writes dots, and old "$" hashes still work."""
        salt = "ab" * 16
        digest = hashlib.pbkdf2_hmac("sha256", b"right password", bytes.fromhex(salt), 1000).hex()
        self.assertTrue(web.password_ok(f"pbkdf2.1000.{salt}.{digest}", "right password"))
        self.assertTrue(web.password_ok(f"pbkdf2$1000${salt}${digest}", "right password"))
        self.assertTrue(web.account_whole(f"pbkdf2.1000.{salt}.{digest}"))
        self.assertFalse(web.account_whole(f"pbkdf2$1000$${digest}"))  # what Compose left of it
        env = os.path.join(tempfile.mkdtemp(), ".env")
        with mock.patch.object(launcher, "ENV", env), mock.patch.object(launcher.getpass, "getpass", lambda prompt: "a-long-password"), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.user(["user", "add", "carol"]), 0)
        with open(env) as f:
            row = f.read().strip()
        self.assertNotIn("$", row)
        self.assertTrue(web.password_ok(row.split(":", 1)[1], "a-long-password"))

    def test_sign_in_and_lockout(self):
        token, why = self.site.sign_in("alice", "right password", "10.0.0.5")
        self.assertEqual(self.site.who(token), "alice")
        self.assertIsNone(self.site.who("made-up"))
        self.site.sign_out(token)
        self.assertIsNone(self.site.who(token))
        for _ in range(5):
            self.assertEqual(self.site.sign_in("alice", "wrong", "10.0.0.6"), (None, "Wrong name or password."))
        self.assertIn("Too many wrong tries", self.site.sign_in("alice", "right password", "10.0.0.6")[1])
        self.assertIsNotNone(self.site.sign_in("alice", "right password", "10.0.0.7")[0])  # another address is not locked
        self.assertEqual(self.site.sign_in("ghost", "right password", "10.0.0.8"), (None, "Wrong name or password."))
        self.assertTrue(any("signed in to the page" in line for line in self.logged))

    def test_page_looks_only(self):
        chat = self.site.chat("alice")
        names = list(chat.session.box.tools)
        self.assertIn("docker_logs", names)
        self.assertEqual([n for n in names if n in ("docker_restart", "docker_stop", "k8s_restart", "k8s_scale", "swarm_scale")], [])
        self.assertFalse(chat.approve("x", "y", "z"))

    def test_a_chat_belongs_to_the_person_who_started_it(self):
        chat = self.site.chat("alice")
        self.assertIs(self.site.chat("alice", chat.id), chat)
        self.assertIsNone(self.site.chat("bob", chat.id))
        self.assertIsNone(self.site.chat("alice", "no-such-chat"))

    def test_a_question_becomes_events_with_each_steps_output_kept(self):
        chat = self.site.chat("alice")
        replies = [call("docker_list"), {"role": "assistant", "content": "web is **down**."}]

        def fake(settings, messages, tools=None, on_text=None, **kw):
            r = replies.pop(0)
            if r["content"] and on_text:
                on_text(r["content"])
            return r, "stop"
        with mock.patch.object(llm, "chat", fake), mock.patch.object(tools, "run", lambda *a, **k: reply(True, "NAMES\nweb Exited")):
            self.assertTrue(chat.run(lambda: chat.ask("why is web down?", "")))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                rows, busy = chat.since(0)
                if not busy:
                    break
                time.sleep(0.01)
        kinds = [e["t"] for e in rows]
        self.assertEqual(kinds, ["ask", "step", "result", "text", "done"])
        self.assertEqual(rows[1]["d"], "Listed containers")
        self.assertIn("web Exited", chat.outputs[1])
        self.assertEqual(chat.since(3)[0][0]["d"], "web is **down**.")
        self.assertTrue(any("[page, alice] You asked: why is web down?" in line for line in self.logged))

    def test_only_one_answer_at_a_time_and_stop_ends_it(self):
        chat = self.site.chat("alice")
        gate = __import__("threading").Event()
        self.assertTrue(chat.run(gate.wait))
        self.assertFalse(chat.run(lambda: None))
        gate.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and chat.since(0)[1]:
            time.sleep(0.01)
        chat.stopped = True
        with self.assertRaises(KeyboardInterrupt):
            chat.text("more")
        with self.assertRaises(KeyboardInterrupt):
            chat.step("a step")

    def test_library_reads_only_inside_its_folders(self):
        with open(os.path.join(self.s.runbook_dir, "host-01", "web.md"), "w") as f:
            f.write("# Runbook: web\nStatus: REVIEWED\n")
        name = cases.save(self.s, NOTE, "")
        report = cases.save_report(self.s, "SUMMARY\nall fine")
        got = self.site.library()
        self.assertEqual([r["id"] for r in got["runbooks"]], ["host-01/web"])
        self.assertEqual(got["runbooks"][0]["status"], "REVIEWED")
        self.assertEqual(len(got["cases"]), 1)
        self.assertEqual([r["id"] for r in got["reports"]], [report])
        self.assertIn("Status: REVIEWED", self.site.document("runbooks", "host-01/web"))
        self.assertIn("all fine", self.site.document("reports", report))
        self.assertIn("PAYMENT_URL", self.site.document("cases", got["cases"][0]["id"]))
        for kind, key in (("runbooks", "../../etc/passwd"), ("runbooks", "host-01/../../x"), ("runbooks", "_cases/x/" + name[:-3]),
                          ("reports", "../../etc/passwd"), ("reports", "x.md"), ("cases", "../x"), ("secrets", "x")):
            self.assertIsNone(self.site.document(kind, key))

    def test_health_keeps_when_each_problem_started_and_what_came_and_went(self):
        rounds = [{"problems": {"a": "host: disk /data 95% used"}, "lines": []},
                  {"problems": {"a": "host: disk /data 96% used", "b": "docker: web: Exited (1)"}, "lines": []},
                  {"problems": {"a": "host: disk /data 96% used"}, "lines": []}]
        with mock.patch.object(watch, "survey", lambda box: rounds.pop(0)):
            for _ in range(3):
                self.site.health.round()
        state = self.site.health.state()
        self.assertEqual([(p["text"], p["known"]) for p in state["problems"]], [("host: disk /data 96% used", True)])
        self.assertEqual([(e["kind"], e["text"]) for e in state["events"]], [("ok", "docker: web: Exited (1)"), ("new", "docker: web: Exited (1)")])
        self.assertEqual(len(web.Health(self.s, lambda line: None).events), 2)  # kept across a restart

    def test_timeline_merges_changes_and_problems_newest_first(self):
        now = int(__import__("time").time())
        history.save(self.s, snapshot(now - 7200))
        history.save(self.s, snapshot(now - 1800, image="shop/web:1.3"))
        self.site.health.events = [{"time": now - 900, "kind": "new", "text": "pod shop/web-1: CrashLoopBackOff"}]
        got = self.site.changes(6)
        self.assertEqual([e["kind"] for e in got["events"]], ["problem", "change"])
        self.assertEqual(got["events"][1]["where"], "a")
        self.assertEqual(got["events"][1]["lane"], "a")
        self.assertEqual(got["lanes"], [self.site.health.box.here])
        self.assertIn(self.site.health.box.here, got["coverage"])
        self.assertIn("image shop/web:1.2 -> shop/web:1.3", got["events"][1]["text"])
        self.assertEqual(self.site.changes(0)["since"], now - 3600 if got["now"] == now else self.site.changes(0)["since"])
        self.assertTrue(got["coverage"][self.site.health.box.here])

    def test_map_shows_each_host_with_its_containers_and_problems_by_lane(self):
        with mock.patch.object(watch, "survey", lambda box: {"problems": {"DOCKER|relay|restarting": "docker: relay: Restarting"},
                                                             "lines": [{"kind": "Kubernetes", "where": "vks-a", "line": "3 nodes", "problems": 0}]}), \
                mock.patch.object(web.Health, "_hosts", lambda self: [{"name": self.box.here, "containers": []}]):
            self.site.health.round()
        state = self.site.health.state()
        self.assertEqual(state["problems"][0]["lane"], self.site.health.box.here)
        self.assertEqual(state["hosts"][0]["name"], self.site.health.box.here)
        self.assertEqual(state["clusters"], [{"name": "vks-a", "kind": "Kubernetes", "line": "3 nodes", "problems": 0,
                                              "role": "", "expired": False, "paused": False, "blind": False,
                                              "context": ""}])

    def test_a_host_that_cannot_say_is_shown_with_the_reason(self):
        patch, s = on("host-01", hub_host="host-01", agent_hosts=("host-02", "host-03"), kube_dir="/none", log_dir=self.s.log_dir)

        def ask(settings, host, tool, args=None, timeout=90):
            if host == "host-02":
                return json.dumps({"containers": [{"name": "relay", "state": "restarting"}]})
            if host == "host-03":
                return "Refused: _map is not a tool this agent answers."
            raise AssertionError
        with patch, mock.patch.object(hosts, "ask", ask), mock.patch.object(watch, "local_map", lambda box: {"containers": []}):
            got = {h["name"]: h for h in web.Health(s, lambda line: None)._hosts()}
        self.assertEqual(got["host-01"]["role"], "hub")
        self.assertEqual(got["host-02"]["containers"][0]["name"], "relay")
        self.assertIn("older than this page", got["host-03"]["error"])

    def test_one_container_shows_its_logs_and_changes(self):
        box = self.site.health.box
        box.tools["docker_logs"].fn = lambda **a: "line 1\nline 2" if a["container"] == "relay" else "?"
        now = int(__import__("time").time())
        self.site.health.events = [{"time": now - 60, "kind": "new", "text": "docker: relay: Restarting", "lane": box.here},
                                   {"time": now - 60, "kind": "new", "text": "docker: web: Exited", "lane": box.here}]
        got = self.site.container(box.here, "relay")
        self.assertEqual(got["logs"], "line 1\nline 2")
        self.assertEqual([e["text"] for e in got["changes"]], ["docker: relay: Restarting"])
        with self.assertRaises(tools.BadInput):
            self.site.container("host-99", "relay")
        with self.assertRaises(tools.BadInput):
            self.site.container(box.here, "../x")

    def test_the_page_loads_nothing_from_elsewhere_and_never_writes_markup(self):
        for text in (webpage.INDEX, webpage.CSS, webpage.JS):
            self.assertNotIn("http://", text)
            self.assertNotIn("https://", text)
        for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "alert(", "confirm(", "prompt("):
            self.assertNotIn(risky, webpage.JS)
        self.assertNotIn(" style=", webpage.INDEX)
        self.assertNotIn("onclick", webpage.INDEX.lower())


class Links(unittest.TestCase):
    def change(self, t, text, lane="host-02", after=None):
        return {"time": t, "after": t if after is None else after, "lane": lane, "text": text}

    def test_a_change_just_before_on_the_same_thing_is_linked(self):
        p = {"time": 1000, "lane": "host-02", "text": "host-02 docker: relay: Up 1 minute (unhealthy)"}
        got = links.find(p, [self.change(940, "container relay: image relay:4 -> relay:5"),
                             self.change(990, "container cache: image cache:1 -> cache:2")], 10)
        self.assertEqual((got["strength"], got["gap"]), ("linked", 60))
        self.assertEqual(links.say(got), "1 min after: container relay: image relay:4 -> relay:5")

    def test_same_host_other_name_is_only_maybe(self):
        p = {"time": 1000, "lane": "host-03", "text": "host-03 docker: web: Exited (1)"}
        got = links.find(p, [self.change(880, "container cache: image cache:1 -> cache:2", "host-03")], 10)
        self.assertEqual(got["strength"], "maybe")
        self.assertTrue(links.say(got).startswith("maybe: 2 min after"))

    def test_no_link_for_another_lane_too_long_before_after_or_a_restart(self):
        p = {"time": 2000, "lane": "host-02", "text": "docker: relay: Restarting"}
        for c in (self.change(1990, "container relay: image a -> b", "host-03"),     # another host
                  self.change(2000 - 16 * 60, "container relay: image a -> b"),      # 16 min before
                  self.change(2300, "container relay: image a -> b", after=2100),    # after the problem
                  self.change(1990, "container relay: started again at 2026-11-17 13:52 UTC"),  # a symptom
                  self.change(1990, "pod shop/relay-1: restarted 6 times"),
                  self.change(1990, "new pod shop/relay-2: restarted 3 times"),
                  self.change(1990, "container relay: state running -> exited; restarted by Docker 2 more times"),
                  self.change(1990, "deployment shop/relay: ready 3 -> 1")):
            self.assertIsNone(links.find(p, [c], 10), c["text"])

    def test_a_real_change_counts_even_beside_a_symptom(self):
        p = {"time": 2000, "lane": "host-02", "text": "docker: relay: Exited (1)"}
        for text in ("container relay: image a -> b; state running -> exited", "new container relay: image a, exited",
                     "container relay is gone", "configmap shop/relay-config: its content changed"):
            self.assertIsNotNone(links.find(p, [self.change(1990, text)], 10), text)

    def test_a_change_seen_only_at_the_next_snapshot_still_counts(self):
        p = {"time": 1000, "lane": "vks-a", "text": "kubernetes vks-a: pod shop/web-7c9: CrashLoopBackOff"}
        got = links.find(p, [self.change(1200, "deployment shop/web: image shop/web:1.2 -> shop/web:1.3", "vks-a", after=900)], 10)
        self.assertEqual((got["strength"], got["gap"]), ("linked", 0))
        self.assertTrue(links.say(got).startswith("at about the same time as"))

    def test_names_of_each_kind(self):
        for text, want in (("container relay: image a -> b", "relay"), ("new container extra: image x", "extra"),
                           ("deployment shop/web: ready 3 -> 1", "shop/web"),
                           ("VKS machine team-a/prod1-w-2 of cluster prod1: Running -> Provisioning", "team-a/prod1-w-2")):
            self.assertEqual(links.subject(text), want)


class Auto(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp()
        self.s = Settings(llm_url="http://model", llm_model="m", kube_dir="/none", log_dir=os.path.join(base, "logs"),
                          runbook_dir=os.path.join(base, "runbooks"), web_users="alice:x", auto_trace_per_hour=2)
        os.makedirs(self.s.log_dir)
        self.logged = []
        with mock.patch.object(web.AutoTrace, "_work", lambda self: None):
            self.site = web.Site(self.s, self.logged.append)
        self.here = self.site.health.box.here

    def rounds(self, *found):
        rounds = [{"problems": p, "lines": []} for p in found]
        with mock.patch.object(watch, "survey", lambda box: rounds.pop(0)), \
                mock.patch.object(web.Health, "_hosts", lambda self: []):
            for _ in found:
                self.site.health.round()

    def test_a_new_problem_is_linked_and_queued_within_the_hourly_limit(self):
        now = int(__import__("time").time())
        changes = [{"time": now - 120, "after": now - 400, "where": "Docker", "text": "container relay: image relay:4 -> relay:5"}]
        with mock.patch.object(history, "record", lambda box: None), mock.patch.object(history, "events", lambda s, since: changes):
            self.rounds({"old": "docker: db: Exited"},
                        {"old": "docker: db: Exited", "a": "docker: relay: unhealthy", "b": "docker: web: Exited",
                         "c": "docker: cache: Exited"})
        got = {p["text"]: p for p in self.site.health.state()["problems"]}
        self.assertIsNone(got["docker: db: Exited"]["trace"])                       # open before the page started
        self.assertEqual(got["docker: relay: unhealthy"]["link"]["strength"], "linked")
        self.assertEqual(got["docker: web: Exited"]["link"]["strength"], "maybe")
        states = sorted(p["trace"]["state"] for t, p in got.items() if t != "docker: db: Exited")
        self.assertEqual(states, ["limit", "waiting", "waiting"])                   # 2 an hour
        self.assertEqual(self.site.health.auto.waiting.qsize(), 2)
        self.assertTrue(any("started 2 min after: container relay" in line for line in self.logged))

    def test_a_new_login_brings_problems_that_are_shown_not_traced(self):
        box = self.site.health.box
        with mock.patch.object(history, "record", lambda box: None), mock.patch.object(history, "events", lambda s, since: []):
            self.rounds({})
            box.first_seen = {**box.first_seen, "prod1": time.time() + 1}  # a login added after the last round
            self.rounds({"KUBERNETES prod1|pod|x": "kubernetes prod1: pod shop/web: CrashLoopBackOff"})
        got = self.site.health.state()["problems"][0]
        self.assertEqual((got["lane"], got["known"], got["trace"]), ("prod1", True, None))
        self.assertEqual(self.site.health.auto.waiting.qsize(), 0)

    def test_off_means_no_trace_at_all(self):
        self.site.health.auto.s = dataclasses.replace(self.site.health.auto.s, auto_trace=False)
        with mock.patch.object(history, "record", lambda box: None), mock.patch.object(history, "events", lambda s, since: []):
            self.rounds({}, {"a": "docker: relay: unhealthy"})
        self.assertIsNone(self.site.health.state()["problems"][0]["trace"])
        self.assertEqual(self.site.health.auto.waiting.qsize(), 0)

    def test_the_trace_runs_and_everyone_can_open_it(self):
        problem = {"text": "docker: relay: unhealthy", "since": 1000, "lane": self.here, "known": False,
                   "link": {"strength": "linked", "text": "container relay: image relay:4 -> relay:5", "time": 940, "gap": 60}}
        self.site.health.open["a"] = dict(problem)
        asked = []

        def ask(chat, question, host):
            asked.append(question)
            chat.step("Read logs of container relay")
            chat.result("connect refused")
            chat.text("Cause: relay:5 needs DB_URL, which is not set.\nFix: add DB_URL and restart relay.")
        with mock.patch.object(web.Chat, "ask", ask):
            self.site.health.auto.run("a", problem)
        trace = self.site.health.state()["problems"][0]["trace"]
        self.assertEqual((trace["state"], trace["cause"]), ("done", "relay:5 needs DB_URL, which is not set."))
        self.assertIn("That change names the same thing: check it first.", asked[0])
        self.assertIn("1 min after: container relay", asked[0])
        self.assertIs(self.site.chat("alice", trace["id"]).user, web.AUTO)       # anyone signed in may open it
        traced = self.site.changes(6)["events"][0]
        self.assertEqual((traced["kind"], traced["id"]), ("traced", trace["id"]))
        self.assertTrue(any("Auto-traced docker: relay: unhealthy" in line for line in self.logged))

    def test_a_trace_is_saved_and_read_back_after_a_restart(self):
        problem = {"text": "docker: relay: unhealthy", "since": 1000, "lane": self.here, "known": False, "link": None}
        self.site.health.open["a"] = dict(problem)

        def ask(chat, question, host):
            chat.add("ask", d=question)
            chat.step("Read logs of container relay")
            chat.result("connect refused")
            chat.text("Cause: DB_URL is not set.\nFix: set DB_URL.")
            chat.session.messages.append({"role": "assistant", "content": "Cause: DB_URL is not set."})
        with mock.patch.object(web.Chat, "ask", ask):
            self.site.health.auto.run("a", problem)
        cid = self.site.health.state()["problems"][0]["trace"]["id"]
        files = os.listdir(os.path.join(self.s.log_dir, "traces", self.here))
        self.assertEqual(len(files), 1)
        listed = autotrace.listing(self.s)[0]
        self.assertEqual((listed["id"], listed["state"], listed["cause"], listed["fix"]),
                         (cid, "done", "DB_URL is not set.", "set DB_URL."))
        with mock.patch.object(web.AutoTrace, "_work", lambda self: None):
            later = web.Site(self.s, self.logged.append)            # a restart: nothing in memory
        chat = later.chat("bob", cid)
        self.assertEqual(chat.outputs[1], "connect refused")
        self.assertEqual(chat.session.messages[-1]["content"], "Cause: DB_URL is not set.")
        self.assertIsNone(later.chat("bob", "x" * 16))

    def test_the_traces_folder_is_capped(self):
        where = autotrace.folder(self.s, "h1")
        os.makedirs(where)
        for n in range(autotrace.TRACES_KEPT + 5):
            with open(os.path.join(where, f"2026{n:06d}-id{n:06d}.json"), "w") as f:
                f.write('{"id": "x"}')
        autotrace._tidy(where)
        left = sorted(os.listdir(where))
        self.assertEqual(len(left), autotrace.TRACES_KEPT)
        self.assertEqual(left[0], "2026000005-id000005.json")    # the oldest went

    def test_alone_runs_the_watch_only_with_a_model_and_auto_trace_on(self):
        started = []
        with mock.patch.object(threading, "Thread", lambda target, args=(), daemon=None: mock.Mock(start=lambda: started.append(args[0]))), \
                mock.patch.object(threading, "Event", lambda: mock.Mock(wait=lambda: None)), \
                mock.patch.object(autotrace, "start", lambda s, log, mapping: mock.Mock()), \
                mock.patch("builtins.print"):
            cli.background(self.s)
            self.assertEqual(started, ["The recorder", "The watch"])
            started.clear()
            cli.background(dataclasses.replace(self.s, auto_trace=False))
            self.assertEqual(started, ["The recorder"])

    def test_cause_line_is_found_and_not_yet_means_none(self):
        self.assertEqual(web.cause_in("**Cause:** disk full\nFix: clean"), "disk full")
        self.assertEqual(web.cause_in("Cause: not found yet"), "")


class Anonymize(unittest.TestCase):
    def setUp(self):
        self.t = anonymize.Table()
        self.t.known(["web-prod-07", "dbhost"], "host")
        self.t.known(["admin@prod-cluster"], "cluster")
        self.t.known(["acme", "lab.example.org"], "name")

    def test_names_and_addresses_go_and_come_back(self):
        text = ("web-prod-07 cannot reach dbhost.lab.example.org at 10.20.1.5:5432 or 10.20.1.9; "
                "context admin@prod-cluster; ACME relay; localhost 127.0.0.1; mask 255.255.255.0")
        hidden = self.t.hide(text)
        for real in ("web-prod-07", "dbhost", "lab.example.org", "10.20.1", "prod-cluster", "ACME"):
            self.assertNotIn(real, hidden)
        self.assertIn("hostA cannot reach hostB.corpA.example at 198.18.0.5:5432 or 198.18.0.9", hidden)  # same /24
        self.assertIn("127.0.0.1", hidden)
        self.assertIn("255.255.255.0", hidden)
        self.assertEqual(self.t.show(hidden), text.replace("ACME", "acme"))

    def test_other_subnets_and_versions(self):
        hidden = self.t.hide("10.20.2.5 and 172.16.0.1 run relay 1.2.3 and 4.5.6.7.8")
        self.assertEqual(hidden, "198.18.0.5 and 198.18.1.1 run relay 1.2.3 and 4.5.6.7.8")
        self.assertEqual(self.t.show("hostA and 198.18.9.9 and hostZ"), "web-prod-07 and 198.18.9.9 and hostZ")

    def test_the_model_sees_stand_ins_and_the_answer_has_real_names(self):
        sent, shown = [], []
        s = Settings(llm_url="http://model", llm_model="m", anonymize=True, anonymize_words="acme", hub_host="")

        def once(settings, body, on_text):
            sent.append(json.dumps(body))
            for piece in ("Restart con", "tainer relay on ho", "stA (198.18", ".0.5)."):
                on_text(piece)
            return {"role": "assistant", "content": "Restart container relay on hostA (198.18.0.5).",
                    "tool_calls": [{"id": "1", "type": "function",
                                    "function": {"name": "docker_logs", "arguments": '{"host": "hostA"}'}}]}, "stop"
        with mock.patch.object(anonymize, "TABLE", self.t), mock.patch.object(llm, "_once", once), \
                mock.patch.object(config, "here", lambda: "web-prod-07"):
            msg, _ = llm.chat(s, [{"role": "system", "content": "You are Tracer on web-prod-07."},
                                  {"role": "tool", "content": "acme relay at 10.20.1.5"}],
                              [{"type": "function", "function": {"name": "x", "description": "on web-prod-07"}}], shown.append)
        self.assertNotIn("web-prod-07", sent[0])
        self.assertNotIn("10.20.1.5", sent[0])
        self.assertNotIn("acme", sent[0])
        self.assertIn("stand for real ones", sent[0])
        self.assertEqual(msg["content"], "Restart container relay on web-prod-07 (10.20.1.5).")
        self.assertEqual(json.loads(msg["tool_calls"][0]["function"]["arguments"]), {"host": "web-prod-07"})
        self.assertEqual("".join(shown), "Restart container relay on web-prod-07 (10.20.1.5).")

    def test_off_sends_everything_as_it_is(self):
        sent = []
        with mock.patch.object(llm, "_once", lambda s, body, on_text: (sent.append(json.dumps(body)), ({"content": "ok"}, "stop"))[1]):
            llm.chat(Settings(llm_url="http://m", llm_model="m"), [{"role": "user", "content": "10.20.1.5"}])
        self.assertIn("10.20.1.5", sent[0])


class Traces(unittest.TestCase):
    def test_list_and_open_a_trace_in_the_terminal(self):
        base = tempfile.mkdtemp()
        s = Settings(llm_url="http://m", llm_model="m", kube_dir="/none", log_dir=base, runbook_dir=base)
        chat = mock.Mock(events=[{"t": "step", "n": 1, "d": "Read logs of container relay"},
                                 {"t": "text", "d": "Cause: DB_URL is not set.\nFix: set DB_URL."}],
                         outputs={1: "connect refused"}, steps=1,
                         record={"id": "abcdefgh123", "host": "h1", "lane": "h1", "problem": "docker: relay: Exited (1)",
                                 "since": 100, "started": 1000, "state": "done", "cause": "DB_URL is not set.",
                                 "link": {"strength": "linked", "text": "container relay: image a -> b", "time": 90, "gap": 10}})
        chat.session.messages = [{"role": "system", "content": "old"}, {"role": "user", "content": "Auto-trace."},
                                 {"role": "assistant", "content": "Cause: DB_URL is not set."}]
        chat.session.box.active = None
        autotrace.save(s, chat)
        session = mock.Mock(messages=[{"role": "system", "content": "new"}])
        session.box.clusters, session.box.agents = {}, []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.traces(s, session, [], lambda line: None)
            cli.traces(s, session, ["1"], lambda line: None)
        said = out.getvalue()
        self.assertIn("docker: relay: Exited (1)", said)
        self.assertIn("likely: DB_URL is not set.", said)
        self.assertIn("1. Read logs of container relay", said)
        self.assertEqual([m["content"] for m in session.messages], ["new", "Auto-trace.", "Cause: DB_URL is not set."])


class Launcher(unittest.TestCase):
    """Runs on the host with its own python3, which may be as old as 3.6."""

    def test_parses_as_python_36(self):
        import ast
        path = os.path.join(os.path.dirname(learn.__file__), "launcher.py")
        with open(path) as f:
            ast.parse(f.read(), feature_version=(3, 6))

    def test_names_match_the_container(self):
        from troubleshooter import launcher
        for raw in ("myapp", "my app", "x.y_z", "ünï"):
            self.assertEqual(launcher.safe(raw), learn.app_name("/apps/" + raw))

    def test_reads_the_proxy_port(self):
        from troubleshooter import launcher
        d = tempfile.mkdtemp()
        log = os.path.join(d, "0")
        with open(log, "w") as f:
            f.write("Starting to serve on 127.0.0.1:45991\n")
        alive = mock.Mock(poll=lambda: None)
        self.assertEqual(launcher.Proxies._port(alive, log), "45991")


class Runbooks(unittest.TestCase):
    def tree(self, files):
        d = tempfile.mkdtemp()
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            with open(os.path.join(d, rel), "w") as f:
                f.write(text)
        return d

    def test_picks_key_files_and_never_secrets(self):
        d = self.tree({"compose.yaml": "x", "README.md": "x", "app/main.py": "x", ".env": "x", "tls.key": "x",
                       "tests/test_a.py": "x", "backup/compose.yaml": "x", ".claude/notes.md": "x",
                       "logs/a.log": "x", "package-lock.json": "x", "photo.png": "x"})
        chosen, _, _ = learn.pick_files(d, 40)
        self.assertEqual(sorted(chosen), ["README.md", "app/main.py", "compose.yaml"])

    def test_docs_cannot_use_up_the_budget(self):
        files = {f"docs/{i}.md": "x" for i in range(50)}
        files.update({"compose.yaml": "x", "main.py": "x"})
        chosen, left, _ = learn.pick_files(self.tree(files), 10)
        self.assertIn("compose.yaml", chosen)
        self.assertIn("main.py", chosen)
        self.assertEqual(left, 42)

    def test_invented_facts_are_dropped(self):
        rows = ["import x", "PORT = 8080", "connect('db1:5432')"]
        reply = ("PORT | 2 | PORT = 8080 | Listens on 8080.\n"
                 "DEPENDS | 3 | connect to redis cluster | Needs Redis on port 6379.\n")
        facts, dropped = learn.verify(reply, "a.py", rows)
        self.assertEqual([f.text for f in facts], ["Listens on 8080."])
        self.assertEqual(dropped, 1)

    def test_wrong_line_number_is_corrected(self):
        facts, _ = learn.verify("PORT | 1 | PORT = 8080 | Listens on 8080.", "a.py", ["import x", "", "PORT = 8080"])
        self.assertEqual(facts[0].line, 3)

    def test_chatter_and_unknown_topics_are_ignored(self):
        facts, _ = learn.verify("Here are the facts:\nNONE\nWEATHER | 1 | import x y | It is sunny today.", "a", ["import x y"])
        self.assertEqual(facts, [])

    def test_line_numbers_survive_masking(self):
        d = self.tree({"a.txt": "one\n-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\npassword=hunter2\nlast"})
        rows = learn.load(os.path.join(d, "a.txt"))
        self.assertEqual(rows[5], "last")
        self.assertNotIn("AAAA", "\n".join(rows))
        self.assertNotIn("hunter2", "\n".join(rows))

    def test_a_long_line_in_a_document_is_kept(self):
        d = self.tree({"a.md": "short\n" * 50 + "x" * 6000 + "\n"})
        self.assertIsNotNone(learn.load(os.path.join(d, "a.md")))

    def test_minified_file_is_skipped(self):
        d = self.tree({"a.js": "x" * 6000})
        self.assertIsNone(learn.load(os.path.join(d, "a.js")))

    def test_learn_turns_thinking_off_and_reports_no_answer(self):
        d = self.tree({"myapp/compose.yaml": "services:\n  web:\n    image: nginx\n", "myapp/main.py": "PORT = 8080\n"})
        s = replace(Settings(), host_root=d, file_roots=("/myapp",), llm_url="http://x", llm_model="m",
                    llm_reasoning="high", runbook_dir=d + "/none")
        seen = []

        def fake(settings, messages, tools=None):
            seen.append(settings.llm_reasoning)
            if "compose.yaml" in messages[-1]["content"]:
                return {"content": ""}, "length"
            if "main.py" in messages[-1]["content"]:
                return {"content": "PORT | 1 | PORT = 8080 | Listens on 8080."}, "stop"
            return {"content": "OVERVIEW: A web app [1]"}, "stop"
        out = io.StringIO()
        with mock.patch.object(llm, "chat", fake), mock.patch.object(llm, "context_tokens", lambda s: None), \
                mock.patch.object(tools.Toolbox, "_docker_rows", lambda self: []), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(learn.main(s, ["/myapp"]), 0)
        self.assertEqual(set(seen), {"off"})
        self.assertIn("No answer for compose.yaml", out.getvalue())

    def test_one_file_cannot_fill_a_section(self):
        many = [learn.Fact("PORT", "big.md", i, f"Fact {i}") for i in range(1, 20)]
        text = learn.render("a", "h", "/a", "m", many + [learn.Fact("PORT", "main.py", 5, "The real port")], [], [], [], [])
        self.assertIn("The real port", text)

    def test_app_code_comes_before_helper_scripts(self):
        d = self.tree({"tools/probe.py": "raise X\n" * 50, "app/worker.py": "x = 1\n"})
        chosen, _, _ = learn.pick_files(d, 1)
        self.assertEqual(chosen, ["app/worker.py"])

    def test_kubeconfig_is_not_sent(self):
        d = self.tree({"a.yaml": "apiVersion: v1\nkind: Config\nusers: []\n"})
        self.assertIsNone(learn.load(os.path.join(d, "a.yaml")))

    def test_chunks_keep_every_line_once(self):
        rows = [f"row {i}" for i in range(100)]
        parts = learn.chunks(rows, 200)
        self.assertGreater(len(parts), 1)
        self.assertEqual(parts[0][0], 1)
        self.assertEqual(parts[-1][1], 100)
        self.assertTrue(all(a[1] + 1 == b[0] for a, b in zip(parts, parts[1:])))

    def book(self, status="DRAFT", source="/apps/myapp"):
        return f"# Runbook: myapp\n\nStatus: {status}\nHost: a\nSource: {source}\n"

    def test_reviewed_runbook_is_not_replaced(self):
        d = self.tree({"host-a/myapp.md": self.book("REVIEWED")})
        s = replace(Settings(), runbook_dir=d)
        self.assertIn("REVIEWED", learn.blocked(s, "host-a", "myapp", "/apps/myapp"))

    def test_draft_is_replaced_and_other_hosts_are_separate(self):
        d = self.tree({"host-a/myapp.md": self.book("REVIEWED"), "host-b/myapp.md": self.book()})
        s = replace(Settings(), runbook_dir=d)
        self.assertIsNone(learn.blocked(s, "host-b", "myapp", "/apps/myapp"))
        self.assertIsNone(learn.blocked(s, "host-c", "myapp", "/apps/myapp"))

    def test_same_name_from_another_folder_is_refused(self):
        d = self.tree({"host-a/myapp.md": self.book(source="/apps2/myapp")})
        s = replace(Settings(), runbook_dir=d)
        self.assertIn("/apps2/myapp", learn.blocked(s, "host-a", "myapp", "/apps/myapp"))

    def test_chat_is_told_a_draft_is_unconfirmed(self):
        d = self.tree({"host-a/myapp.md": self.book(), "host-a/done.md": self.book("REVIEWED")})
        box = tools.Toolbox(replace(Settings(), runbook_dir=d))
        self.assertIn("UNCONFIRMED", box.read_runbook("myapp", "host-a"))
        self.assertNotIn("UNCONFIRMED", box.read_runbook("done", "host-a"))
        self.assertIn("host-a/myapp (DRAFT)", box.list_runbooks())

    def test_this_hosts_runbooks_come_first(self):
        d = self.tree({"aaa/x.md": self.book(), "here/x.md": self.book()})
        box = tools.Toolbox(replace(Settings(), runbook_dir=d))
        with mock.patch("socket.gethostname", return_value="here"):
            self.assertEqual(box._runbooks()[0][0], "here")

    def test_runbook_path_cannot_escape(self):
        d = self.tree({"host-a/myapp.md": self.book()})
        box = tools.Toolbox(replace(Settings(), runbook_dir=d))
        for app, host in (("../../etc/passwd", "host-a"), ("myapp", "../..")):
            with self.assertRaises(tools.BadInput):
                box.read_runbook(app, host)

    def test_nothing_on_stdout_but_the_runbook(self):
        d = self.tree({"myapp/compose.yaml": "services:\n  web:\n    ports: ['8080:80']\n"})
        s = replace(Settings(), host_root=d, file_roots=("/myapp",), llm_url="http://x", llm_model="m",
                    runbook_dir=d + "/none")
        replies = iter([({"content": "PORT | 3 | ports: ['8080:80'] | Web listens on 8080."}, "stop"),
                        ({"content": "OVERVIEW: A web app [1]\nCHECK: Port 8080 [1]\nCHECK: Made up [99]"}, "stop")])
        out = io.StringIO()
        with mock.patch.object(llm, "chat", lambda *a, **k: next(replies)), \
                mock.patch.object(llm, "context_tokens", lambda s: None), \
                mock.patch.object(tools.Toolbox, "_docker_rows", lambda self: []), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(learn.main(s, ["/myapp"]), 0)
        text = out.getvalue()
        self.assertTrue(text.startswith("# Runbook: myapp"))
        self.assertIn("Status: DRAFT", text)
        self.assertIn("(compose.yaml:3)", text)
        self.assertNotIn("Made up", text)

    def test_outside_file_roots_is_refused(self):
        d = self.tree({"etc/x.conf": "x"})
        s = replace(Settings(), host_root=d, file_roots=("/apps",), llm_url="http://x", llm_model="m")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(learn.main(s, ["/etc"]), 2)


def stamp(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


class VsphereScope(unittest.TestCase):
    """Three logins, as kubectl vsphere login writes them: the Supervisor itself (named by its address, no
    namespace), a vSphere namespace (team-a, twice: two contexts on the same namespace), and a VKS workload cluster."""

    def setUp(self):
        self.calls, self.expired = [], set()
        self.box = tools.Toolbox(Settings(), clusters={
            "sup-ip": tools.Cluster("sup-ip", "/k", "sup-ip", api="https://sup"),
            "team-a": tools.Cluster("team-a", "/k", "team-a", namespace="team-a", api="https://sup"),
            "team-a-again": tools.Cluster("team-a-again", "/k", "team-a-again", namespace="team-a", api="https://sup"),
            "prod1": tools.Cluster("prod1", "/k", "prod1", api="https://prod1")})
        patch = mock.patch.object(tools, "run", self.kubectl)
        patch.start()
        self.addCleanup(patch.stop)

    def kubectl(self, argv, timeout=30, parsed=False):
        self.calls.append(argv)
        ctx = argv[argv.index("--context") + 1]
        if ctx in self.expired:
            return reply(False, "error: You must be logged in to the server (Unauthorized)")
        supervisor = ctx != "prod1"
        if "/apis/cluster.x-k8s.io" in argv:
            return reply(True, "{}") if supervisor else reply(False, "Error from server (NotFound): not found")
        if "events" in argv:
            return reply(True, "")
        kind = argv[argv.index("get") + 1]
        if supervisor:  # a namespace user: nothing across the Supervisor, everything in team-a
            if "--all-namespaces" in argv or kind == "nodes" or ("-n" in argv and argv[argv.index("-n") + 1] != "team-a"):
                return reply(False, f"Error from server (Forbidden): {kind} is forbidden")
            items = {"persistentvolumeclaims": [vks_item("data-1", status={"phase": "Bound"}),
                                                vks_item("data-2", status={"phase": "Pending"})],
                     "pods": []}.get(kind, VKS.get(kind, []))
            return reply(True, json.dumps({"items": items}))
        if kind == "nodes":
            return reply(True, json.dumps({"items": [{"metadata": {"name": "n1"}, "status": {"conditions": [
                {"type": "Ready", "status": "True"}]}}]}))
        old = pod("old-restarts", restarts=9, ended=stamp(40 * 86400))
        crash = pod("crashing", ready=False, restarts=6, ended=stamp(120))
        evicted = {"metadata": {"name": "evicted", "namespace": "shop"},
                   "status": {"phase": "Failed", "reason": "Evicted", "startTime": stamp(30 * 86400)}}
        fresh = {"metadata": {"name": "just-failed", "namespace": "shop"},
                 "status": {"phase": "Failed", "reason": "Error", "startTime": stamp(600)}}
        return reply(True, json.dumps({"items": [old, crash, evicted, fresh, pod("fine")]}))

    def survey(self):
        got = watch.kube_survey(self.box)
        return got, {row["where"]: row for row in got["lines"]}

    def test_each_login_gets_its_own_kind_of_card(self):
        got, cards = self.survey()
        self.assertEqual(cards["sup-ip"]["role"], "supervisor")
        self.assertIn("sees only its vSphere namespaces", cards["sup-ip"]["line"])
        self.assertEqual(cards["sup-ip"]["problems"], 0)
        ns = cards["team-a"]
        self.assertEqual(ns["role"], "namespace")
        self.assertIn("2 VKS cluster(s) (other, prod1)", ns["line"])
        self.assertIn("3 VMs", ns["line"])
        self.assertEqual(ns["problems"], 1)  # the claim that is not bound; Forbidden is never one
        self.assertEqual([v for v in got["problems"].values() if "claim" in v],
                         ["kubernetes team-a: claim data-2 (in the vSphere namespace): Pending, used by nothing"])
        self.assertNotIn("team-a-again", cards)  # the same namespace through a second context: one card
        self.assertEqual(cards["team-a/prod1"]["role"], "vks")
        self.assertEqual(cards["team-a/prod1"]["context"], "team-a")
        self.assertGreater(cards["team-a/prod1"]["problems"], 0)
        self.assertEqual(cards["prod1"]["role"], "cluster")
        self.assertNotIn("forbidden", " ".join(got["problems"].values()).lower())

    def test_a_vsphere_namespace_is_looked_at_once(self):
        self.survey()
        scans = [c for c in self.calls if "clusters.cluster.x-k8s.io" in c and "-n" in c]
        self.assertEqual({c[c.index("--context") + 1] for c in scans}, {"team-a"})
        health = [c for c in self.calls if "kubeadmcontrolplanes.controlplane.cluster.x-k8s.io" in c and "-n" in c]
        self.assertEqual(len(health), 1)

    def test_old_restarts_and_old_failed_pods_are_not_problems(self):
        got, cards = self.survey()
        said = [v for k, v in got["problems"].items() if k.startswith("KUBERNETES prod1")]
        self.assertTrue(any("crashing" in v and "restarted 6 times, last 2 min ago" in v for v in said), said)
        self.assertTrue(any("just-failed" in v for v in said))
        self.assertFalse(any("old-restarts" in v or "evicted" in v for v in said))
        self.assertIn("1 old failed pod(s) left over", cards["prod1"]["line"])
        # a tool, asked about the pod, still says it
        self.assertIn("restarted 9 times, last 40 days ago", tools.Toolbox._pod_trouble(pod("old-restarts", restarts=9, ended=stamp(40 * 86400))))

    def test_an_old_unused_claim_is_left_over_not_a_problem(self):
        real = self.kubectl

        def old(argv, timeout=30, parsed=False):
            if "persistentvolumeclaims" in argv and "--context" in argv and argv[argv.index("--context") + 1] != "prod1":
                return reply(True, json.dumps({"items": [
                    vks_item("left", spec={"volumeName": "gone"}, status={"phase": "Pending"}) | {
                        "metadata": {"name": "left", "namespace": "team-a", "creationTimestamp": stamp(98 * 86400)}}]}))
            return real(argv, timeout, parsed)
        self.kubectl = old
        with mock.patch.object(tools, "run", old):
            got, cards = self.survey()
        self.assertEqual(cards["team-a"]["problems"], 0)
        self.assertIn("1 old unused claim(s) not bound", cards["team-a"]["line"])

    def test_a_namespace_this_login_cannot_see_is_not_called_healthy(self):
        real = self.kubectl

        def blind(argv, timeout=30, parsed=False):
            ctx = argv[argv.index("--context") + 1]
            if ctx.startswith("team-a") and "get" in argv and "--raw" not in argv and "events" not in argv:
                return reply(False, "Error from server (Forbidden): forbidden")
            return real(argv, timeout, parsed)
        with mock.patch.object(tools, "run", blind):
            got, cards = self.survey()
        self.assertTrue(cards["team-a"]["blind"])
        self.assertFalse(cards["prod1"]["blind"])

    def test_an_expired_login_is_said_not_counted(self):
        self.expired = {"team-a", "team-a-again", "prod1"}
        self.box.roles.pop("prod1", None)
        got, cards = self.survey()
        self.assertTrue(cards["prod1"]["expired"])
        self.assertIn("login expired", cards["prod1"]["line"])
        self.assertTrue(cards["team-a"]["expired"])
        self.assertEqual(cards["team-a"]["problems"], 0)
        self.assertFalse(any(k.startswith(("KUBERNETES prod1", "KUBERNETES team-a")) for k in got["problems"]))


class PausedVks(unittest.TestCase):
    """A paused VKS cluster runs fine: VMware just stops managing it. Said once, never per part."""

    def got(self, paused):
        cond = [{"type": "Paused", "status": "True" if paused else "False", "reason": "Paused" if paused else "NotPaused"},
                {"type": "Updating", "status": "False", "reason": "NotUpdating"}]
        machine = vks_item("dev-cp-1", OF, {"clusterName": "prod1"}, {"phase": "Running", "nodeRef": {"name": "dev-cp-1"},
                                                                         "v1beta2": {"conditions": cond}})
        return {"clusters": ([], ""), "planes": ([vks_item("prod1-cp", OF, {"replicas": 1}, {"readyReplicas": 1, "conditions": cond})], ""),
                "pools": ([vks_item("prod1-pool", OF, {"replicas": 1}, {"readyReplicas": 1, "conditions": cond})], ""),
                "machines": ([machine], ""),
                "vms": ([vks_item("dev-cp-1", status={"powerState": "PoweredOn", "vmIp": "10.0.0.7"})], "")}

    def cluster(self, paused):
        return vks_item("prod1", spec={"paused": paused}, status={"phase": "Provisioned", "conditions": [
            {"type": "Paused", "status": "True" if paused else "False"}]})

    def test_paused_is_said_once_and_machines_still_count(self):
        rows = tools.Toolbox._vks_cluster(self.cluster(True), self.got(True))
        self.assertTrue(rows[0].startswith("VKS CLUSTER team-a/prod1: paused: VMware won't repair"), rows[0])
        self.assertIn("1 of 1 machines running; nothing wrong found", rows[0])
        self.assertEqual(rows[1:], [])

    def test_what_the_pause_causes_is_not_said_again(self):
        c = self.cluster(True)
        c["status"]["conditions"] += [{"type": "TopologyReconciled", "status": "False", "reason": "Paused",
                                       "message": "Cluster spec.paused is set to true"},
                                      {"type": "UpdatesAvailable", "status": "False", "reason": "AlreadyUpToDate"}]
        rows = tools.Toolbox._vks_cluster(c, self.got(True))
        self.assertEqual(rows[1:], [])
        c = self.cluster(False)
        c["status"]["conditions"] += [{"type": "UpdatesAvailable", "status": "False", "reason": "AlreadyUpToDate"},
                                      {"type": "Available", "status": "False", "reason": "NotAvailable"}]
        rows = tools.Toolbox._vks_cluster(c, self.got(False))
        self.assertEqual(rows[1:], ["- cluster: Available=False (NotAvailable)"])  # a real one still shows

    def test_who_paused_it_and_when(self):
        c = self.cluster(True)
        c["metadata"]["managedFields"] = [
            {"manager": "capi-controller", "operation": "Update", "time": "2026-11-01T01:00:00Z",
             "fieldsV1": {"f:status": {"f:phase": {}}}},
            {"manager": "kubectl-patch", "operation": "Update", "time": "2026-08-10T14:02:33Z",
             "fieldsV1": {"f:spec": {"f:paused": {}}}}]
        rows = tools.Toolbox._vks_cluster(c, self.got(True))
        self.assertIn("unpaused (set by kubectl-patch, its last change 2026-08-10 14:02 UTC);", rows[0])
        self.assertEqual(tools.paused_by(self.cluster(True)), "")

    def test_updating_false_is_normal(self):
        rows = tools.Toolbox._vks_cluster(self.cluster(False), self.got(False))
        self.assertEqual(rows, ["VKS CLUSTER team-a/prod1: 1 of 1 machines running; nothing wrong found"])
        self.assertEqual(tools.conditions({"status": {"conditions": [{"type": "Updating", "status": "True"}]}}), ["Updating=True"])


class KubeHome(unittest.TestCase):
    """Your ~/.kube/config, mounted: its contexts keep their names and namespaces, and a new login is read again."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.dir = tempfile.mkdtemp()
        self.cfg = {"clusters": [{"name": "sup", "cluster": {"server": "https://sup"}}],
                    "users": [{"name": "me", "user": {}}],
                    "contexts": [{"name": "10.0.0.9", "context": {"cluster": "sup", "user": "me"}},
                                 {"name": "team-a", "context": {"cluster": "sup", "user": "me", "namespace": "team-a"}}]}
        self.write()
        patch = mock.patch.object(tools, "_read_kubeconfig", lambda path: self.cfg)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, extra=""):
        path = os.path.join(self.home, "config")
        with open(path, "w") as f:
            f.write("x" + extra)
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9 * len(extra)))

    def test_contexts_keep_their_names_namespaces_and_server(self):
        found = tools.find_clusters(self.dir, self.home)
        self.assertEqual(sorted(found), ["10.0.0.9", "team-a"])
        self.assertEqual(found["team-a"].namespace, "team-a")
        self.assertEqual(found["team-a"].api, "https://sup")
        self.assertEqual(found["10.0.0.9"].namespace, "")

    def test_a_new_login_shows_up_without_a_restart(self):
        logged = []
        box = tools.Toolbox(Settings(kube_dir=self.dir, kube_home=self.home))
        box.log = logged.append
        self.assertEqual(sorted(box.clusters), ["10.0.0.9", "team-a"])
        self.assertFalse(box.refresh())  # nothing changed: nothing read
        self.cfg["contexts"].append({"name": "prod1", "context": {"cluster": "sup", "user": "me"}})
        self.write("more")
        self.assertTrue(box.refresh())
        self.assertIn("prod1", box.clusters)
        self.assertIn("prod1", box.tools["k8s_use_context"].params["context"].enum)
        self.assertEqual(logged, ["The Kubernetes logins changed: added prod1"])

    def test_a_folder_file_wins_over_the_same_name_at_home(self):
        with open(os.path.join(self.dir, "team-a.yaml"), "w") as f:
            f.write("x")
        one = {**self.cfg, "contexts": self.cfg["contexts"][1:]}
        with mock.patch.object(tools, "_read_kubeconfig", lambda path: one if path.endswith(".yaml") else self.cfg):
            found = tools.find_clusters(self.dir, self.home)
        self.assertEqual(found["team-a"].file, os.path.join(self.dir, "team-a.yaml"))


if __name__ == "__main__":
    unittest.main()
