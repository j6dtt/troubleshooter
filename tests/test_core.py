"""python -m unittest discover tests  (no network, no Docker needed)"""
import io
import json
import os
import ssl
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from unittest import mock

from troubleshooter import cli, engine, history, learn, llm, tools, watch
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


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}




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
        self.assertIn("none of your contexts is a Supervisor", out)

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

    def test_claim_must_be_asked_from_the_workload_cluster(self):
        with self.assertRaises(tools.BadInput):
            self.box.vks_volume_check("super", "data", "shop")
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
    def run_watch(self, rounds_of_overview, argv=()):
        box = mock.Mock(clusters={}, roles={})
        box._disks.return_value = [(95, "/data: 95% used, 1.0 GB free of 20.0 GB"), (40, "/: 40% used")]
        box._meminfo.return_value = {"MemAvailable": 8000, "MemTotal": 16000}
        box.overview.side_effect = rounds_of_overview
        out, logged = io.StringIO(), []
        with redirect_stdout(out), mock.patch.object(history, "record", lambda b: None):
            code = watch.main(Settings(), box, list(argv), logged.append, sleep=lambda s: None, rounds=len(rounds_of_overview))
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
        _, out, _ = self.run_watch(["KUBERNETES vks: not reachable: the login was refused: the token has expired or is wrong."])
        self.assertIn("PROBLEM   kubernetes vks: not reachable: the login was refused", out)

    def test_a_failed_round_does_not_end_the_watch(self):
        _, out, _ = self.run_watch([RuntimeError("docker is down"), "DOCKER: 1 of 1 containers running; 0 need a look"])
        self.assertIn("This round of checks failed (docker is down)", out)

    def test_bad_arguments(self):
        self.assertEqual(self.run_watch([], argv=["--often"])[0], 2)


class Report(unittest.TestCase):
    def test_report_uses_the_conversation_and_is_saved(self):
        s = Settings(llm_url="http://x", llm_model="m", kube_dir="/none", log_dir=tempfile.mkdtemp())
        ui = UI()
        ui.spinner, ui.done = mock.Mock(), lambda: None
        sess = engine.Session(s, ui)
        self.assertIsNone(sess.report())
        self.assertIn("Nothing to report yet", ui.notices[0])
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


if __name__ == "__main__":
    unittest.main()
