"""Clean text before the model, the screen or a log file sees it.

Masks secrets, removes terminal control codes (a log line must not be able to
drive the terminal) and cuts long output down to size.
"""
import re

MASK = "***"
_WORD = (r"(?:password|passwd|passphrase|pass(?=[_\-.=:\"'\s\\]|$)|pwd|secret|token|"
         r"api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?key|"
         r"key(?=[_\-.=:\"'\s\\]|$)|credential|authorization|cookie)")
# Names that hold a pointer to a secret, not the secret: useful and harmless.
_POINTER = re.compile(r"(name|ref|file|path|expir\w*|seconds|ttl|count|length|type|id)$", re.I)
_NAME = rf"[\w.\-]*{_WORD}[\w.\-]*"
_SEP = r"\\?[\"']?\s*[=:]\s*"

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S)
_ENV_PAIR = re.compile(
    rf"(\\?\"?name\\?\"?\s*:\s*\\?[\"']?{_NAME}\\?[\"']?\s*,?\s*\\?\"?value\\?\"?\s*:\s*)(\\?[\"']?)[^\n\"'\\]*",
    re.I)
_QUOTED = re.compile(rf"({_NAME})({_SEP})(\\?[\"'])(.*?)(\3)", re.I)
_PLAIN = re.compile(rf"({_NAME})({_SEP})(?![\"'\\]|{re.escape(MASK)})([^\s,;&}}\]\"'\\]+)", re.I)
_BEARER = re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{6,}", re.I)
_URL = re.compile(r"([a-z][a-z0-9+.\-]*://[^/\s:@]*):[^/\s@]+@", re.I)
_TOKENS = re.compile(
    r"\beyJ[\w\-]{5,}\.[\w\-]{5,}\.[\w\-]+|\bsk-[A-Za-z0-9_\-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bglpat-[\w\-]{16,}|\bAKIA[0-9A-Z]{16}\b|\bxox[abprs]-[\w\-]{10,}")
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b.|[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")


def _keep(match, value):
    if _POINTER.search(match.group(1)):
        return match.group(0)
    return value


def mask(text):
    text = _PEM.sub("[private key removed]", text)
    text = _ENV_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
    text = _URL.sub(rf"\1:{MASK}@", text)
    text = _QUOTED.sub(lambda m: _keep(m, f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(5)}"), text)
    text = _PLAIN.sub(lambda m: _keep(m, f"{m.group(1)}{m.group(2)}{MASK}"), text)
    return _TOKENS.sub(MASK, text)


def strip_control(text):
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def cut(text, limit):
    """Keep the start and, mostly, the end: the newest log lines matter most."""
    if len(text) <= limit:
        return text
    head, tail = limit * 3 // 10, limit * 7 // 10
    dropped = text[head:len(text) - tail].count("\n") + 1
    return f"{text[:head]}\n[... {dropped} lines cut to save space; narrow the search to see them ...]\n{text[-tail:]}"


def clean(text, limit):
    return cut(mask(strip_control(text)), limit)
