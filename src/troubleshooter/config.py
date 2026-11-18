"""Settings, read from environment variables set in compose.yaml (secrets in .env)."""
import dataclasses
import os
import socket
from dataclasses import dataclass, field


def _flag(name, default):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    llm_url: str = ""
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_verify_tls: bool = False
    llm_ca_file: str = ""
    llm_timeout: int = 300
    llm_max_tokens: int = 8192
    llm_reasoning: str = ""
    llm_busy_wait: int = 75
    max_steps: int = 15
    max_seconds: int = 900
    max_context_chars: int = 200_000
    tool_output_chars: int = 12_000
    allow_changes: bool = True
    protected_names: tuple = ()
    file_roots: tuple = ("/apps2",)
    host_root: str = "/host"
    log_dir: str = "/logs"
    kube_dir: str = "/kube"
    runbook_dir: str = "/runbooks"
    learn_max_files: int = 40
    learn_minutes: int = 30
    learn_llm_url: str = ""
    learn_llm_model: str = ""
    learn_llm_api_key: str = field(default="", repr=False)
    record_minutes: int = 5
    kubernetes: bool = True
    auto_trace: bool = True
    auto_trace_per_hour: int = 10
    link_minutes: int = 10
    hub_host: str = ""            # "name" or "name=address"; empty = this host stands alone
    agent_hosts: tuple = ()       # each "name" or "name=address"
    agent_port: int = 8001
    web_port: int = 9001
    agent_key: str = field(default="", repr=False)
    tls_key: str = field(default="", repr=False)      # the private key, base64 of its PEM
    tls_cert: str = "/certs/troubleshooter-cert.pem"
    web_users: str = field(default="", repr=False)    # "name:hash,name:hash"
    web_tls_key: str = field(default="", repr=False)  # your own certificate's key for the page, base64 of its PEM
    web_cert: str = "/certs/web-cert.pem"
    helper_image: str = "troubleshooter-runtime:2"


def load():
    return Settings(
        llm_url=os.environ.get("LLM_URL", "").rstrip("/"),
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_verify_tls=_flag("LLM_VERIFY_TLS", "false"),
        llm_ca_file=os.environ.get("LLM_CA_FILE", ""),
        llm_timeout=int(os.environ.get("LLM_TIMEOUT_SECONDS", "300")),
        llm_max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "8192")),
        llm_reasoning=os.environ.get("LLM_REASONING", "").strip().lower(),
        llm_busy_wait=int(os.environ.get("LLM_BUSY_WAIT_SECONDS", "75")),
        max_steps=int(os.environ.get("MAX_STEPS_PER_QUESTION", "15")),
        max_seconds=int(os.environ.get("MAX_SECONDS_PER_QUESTION", "900")),
        max_context_chars=int(os.environ.get("MAX_CONTEXT_CHARS", "200000")),
        tool_output_chars=int(os.environ.get("TOOL_OUTPUT_CHARS", "12000")),
        allow_changes=_flag("ALLOW_CHANGES", "true"),
        protected_names=tuple(_list("PROTECTED_NAMES", "troubleshooter")),
        file_roots=tuple(_list("FILE_ROOTS", "/apps2")),
        host_root=os.environ.get("HOST_ROOT", "/host"),
        log_dir=os.environ.get("LOG_DIR", "/logs"),
        kube_dir=os.environ.get("KUBE_DIR", "/kube"),
        runbook_dir=os.environ.get("RUNBOOK_DIR", "/runbooks"),
        learn_max_files=int(os.environ.get("LEARN_MAX_FILES", "40")),
        learn_minutes=int(os.environ.get("LEARN_MINUTES", "30")),
        learn_llm_url=os.environ.get("LEARN_LLM_URL", "").rstrip("/"),
        learn_llm_model=os.environ.get("LEARN_LLM_MODEL", ""),
        learn_llm_api_key=os.environ.get("LEARN_LLM_API_KEY", ""),
        record_minutes=int(os.environ.get("RECORD_MINUTES", "5") or 0),
        kubernetes=_flag("KUBERNETES", "on"),
        auto_trace=_flag("AUTO_TRACE", "on"),
        auto_trace_per_hour=int(os.environ.get("AUTO_TRACE_PER_HOUR", "10") or 0),
        link_minutes=int(os.environ.get("LINK_MINUTES", "10") or 10),
        hub_host=os.environ.get("HUB_HOST", "").strip(),
        agent_hosts=tuple(_list("AGENT_HOSTS")),
        agent_port=int(os.environ.get("AGENT_PORT", "8001") or 8001),
        web_port=int(os.environ.get("WEB_PORT", "9001") or 9001),
        agent_key=os.environ.get("AGENT_KEY", ""),
        tls_key=os.environ.get("TLS_KEY", ""),
        tls_cert=os.environ.get("TLS_CERT", "/certs/troubleshooter-cert.pem"),
        web_users=os.environ.get("WEB_USERS", ""),
        web_tls_key=os.environ.get("WEB_TLS_KEY", ""),
        web_cert=os.environ.get("WEB_CERT", "/certs/web-cert.pem"),
        helper_image=os.environ.get("HELPER_IMAGE", "troubleshooter-runtime:2"),
    )


def for_learning(s):
    """The settings ./ts learn talks to its model with: the chat's, unless a learn model is set.

    The chat's key is only sent to the chat's server: another server gets LEARN_LLM_API_KEY, or none.
    """
    if not (s.learn_llm_url or s.learn_llm_model):
        return s
    url = s.learn_llm_url or s.llm_url
    key = s.learn_llm_api_key or (s.llm_api_key if url == s.llm_url else "")
    return dataclasses.replace(s, llm_url=url, llm_model=s.learn_llm_model or s.llm_model, llm_api_key=key)


def short(host):
    """A host's name as compared here: the part before the first dot, in lower case."""
    return host.split("=")[0].strip().split(".")[0].lower()


def here():
    return short(socket.gethostname())


def role(s):
    """alone: no hub is set. hub: this host is it. agent: another host is."""
    if not s.hub_host:
        return "alone"
    return "hub" if short(s.hub_host) == here() else "agent"


def address(entry):
    """Where to reach a host written as "name" or "name=address"."""
    name, _, where = entry.partition("=")
    return (where or name).strip()


def agents(s):
    """{name: address} of the other hosts the hub may ask. Empty unless this host is the hub."""
    if role(s) != "hub":
        return {}
    return {short(e): address(e) for e in s.agent_hosts if short(e) != here()}
