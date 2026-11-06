"""Settings, read from environment variables set in compose.yaml (secrets in .env)."""
import os
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
        helper_image=os.environ.get("HELPER_IMAGE", "troubleshooter-runtime:2"),
    )
