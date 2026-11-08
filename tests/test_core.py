"""python -m unittest discover tests  (no network, no Docker needed)"""
import io
import json
import os
import ssl
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from unittest import mock

from troubleshooter import cli, engine, learn, llm, tools
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
