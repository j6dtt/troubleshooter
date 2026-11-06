"""python -m unittest discover tests  (no network, no Docker needed)"""
import io
import json
import os
import ssl
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest import mock

from troubleshooter import cli, engine, llm, tools
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
        self.assertEqual(body["reasoning_effort"], "minimal")


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


if __name__ == "__main__":
    unittest.main()
