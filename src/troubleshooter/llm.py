"""Client for any OpenAI-compatible chat server (vLLM, NIM, gateways)."""
import json
import re
import ssl
import time
import urllib.error
import urllib.request

from .redact import mask, strip_control

_THINK = re.compile(r"<think>.*?(</think>|\Z)", re.S)


class LLMError(Exception):
    """A model server problem, worded for the person at the terminal. code: the HTTP status, if any."""
    code = None


class Busy(LLMError):
    """Rate-limited or briefly failing: worth waiting and trying again."""

    def __init__(self, text, retry_after=None):
        super().__init__(text)
        self.retry_after = retry_after


class ThinkFilter:
    """Hide <think>...</think> from streamed text, even split across chunks."""
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf, self.inside = "", False

    def feed(self, text):
        self.buf += text
        out = ""
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            at = self.buf.find(tag)
            if at >= 0:
                if not self.inside:
                    out += self.buf[:at]
                self.buf, self.inside = self.buf[at + len(tag):], not self.inside
                continue
            keep = next((n for n in range(len(tag) - 1, 0, -1) if self.buf.endswith(tag[:n])), 0)
            if not self.inside:
                out += self.buf[:len(self.buf) - keep]
            self.buf = self.buf[len(self.buf) - keep:] if keep else ""
            break
        return out

    def flush(self):
        out, self.buf = ("" if self.inside else self.buf), ""
        return out


def strip_think(text):
    text = _THINK.sub("", text)
    if "</think>" in text:  # some templates open the block before the reply starts
        text = text.split("</think>")[-1]
    return text.strip()


def _ssl_context(s):
    """The llm-verify-tls setting decides whether the model server's certificate is checked."""
    if not s.llm_url.startswith("https"):
        return None
    if not s.llm_verify_tls:
        return ssl._create_unverified_context()
    try:
        return ssl.create_default_context(cafile=s.llm_ca_file or None)
    except (OSError, ssl.SSLError) as e:
        raise LLMError(f"Cannot use the CA file {s.llm_ca_file}: {e}") from None


def _request(s, path, body=None):
    headers = {"Content-Type": "application/json"}
    if s.llm_api_key:
        headers["Authorization"] = f"Bearer {s.llm_api_key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(s.llm_url + path, data=data, headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=s.llm_timeout, context=_ssl_context(s))
    except urllib.error.HTTPError as e:
        detail = e.read(2000).decode("utf-8", "replace")
        if e.code in (429, 500, 502, 503, 504):
            err = Busy(_explain(e.code, detail, s, path), e.headers.get("Retry-After"))
        else:
            err = LLMError(_explain(e.code, detail, s, path))
        err.code = e.code
        raise err from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise LLMError(f"The model server's certificate failed the check: {reason.verify_message}. "
                           "Set llm-ca-file to its CA, or llm-verify-tls to false.") from None
        if isinstance(reason, TimeoutError):
            raise Busy(f"The model server gave no reply within {s.llm_timeout} seconds.") from None
        raise LLMError(f"Cannot reach the model server at {s.llm_url}: {reason}") from None


def _reason(detail):
    """The server's own words: they usually say exactly what is wrong."""
    try:
        err = json.loads(detail).get("error")
        text = err.get("message") if isinstance(err, dict) else err
    except (ValueError, AttributeError):
        text = None
    text = re.sub(r"\s+", " ", strip_control(str(text or detail))).strip()
    return mask(text)[:300] or "nothing"


def _explain(code, detail, s, path=""):
    said = _reason(detail)
    if code in (401, 403):
        return f"The model server refused access (HTTP {code}). Check LLM_API_KEY in .env. Server said: {said}"
    if code == 404:
        return (f"Not found (HTTP 404) at {s.llm_url}{path}. Server said: {said}. "
                f"Check llm-url (usually ends in /v1) and llm-model ({s.llm_model}).")
    if code == 400 and "reasoning" in detail.lower():
        return (f"The model server refused the thinking setting (llm-reasoning: {s.llm_reasoning or 'empty'}). "
                f"Leave llm-reasoning empty. Server said: {said}")
    if code == 400 and "tool" in detail.lower():
        return ("The model server rejected tool calling (HTTP 400). Tool calling must be switched on "
                f"for this model on the server. Server said: {detail[:200]}")
    if code == 400 and re.search(r"context|too long|maximum", detail, re.I):
        return "The conversation is too long for this model. Type /new, or lower MAX_CONTEXT_CHARS."
    if code == 429:
        return "The model server is rate-limiting (HTTP 429)."
    if code >= 500:
        return f"The model server had a problem (HTTP {code})."
    return f"The model server returned HTTP {code}: {detail[:300]}"


def models(s):
    with _request(s, "/models") as r:
        return [m.get("id", "?") for m in json.load(r).get("data", [])]


def context_tokens(s):
    """The model's context window, if the server says (vLLM does); otherwise None."""
    try:
        with _request(s, "/models") as r:
            for m in json.load(r).get("data", []):
                if m.get("id") == s.llm_model:
                    return m.get("max_model_len")
    except (LLMError, ValueError):
        pass
    return None


def _merge_calls(calls, deltas):
    for d in deltas or []:
        c = calls.setdefault(d.get("index", len(calls)), {"id": "", "name": "", "args": ""})
        fn = d.get("function") or {}
        c["id"] = d.get("id") or c["id"]
        c["name"] += fn.get("name") or ""
        c["args"] += fn.get("arguments") or ""


def _events(resp):
    """Yield parsed stream chunks; also accepts a plain JSON reply from gateways that ignore stream."""
    if "text/event-stream" not in resp.headers.get("Content-Type", ""):
        reply = json.load(resp)
        choice = (reply.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = [dict(c, index=i) for i, c in enumerate(msg.get("tool_calls") or [])]
        yield {"choices": [{"delta": {"content": msg.get("content"), "tool_calls": calls},
                            "finish_reason": choice.get("finish_reason")}]}
        return
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data:"):
            line = line[5:].strip()
            if line == "[DONE]":
                return
            yield json.loads(line)


def _body(s, messages, tools):
    body = {"model": s.llm_model, "messages": messages, "stream": True}
    gpt = re.search(r"gpt-5|\bo[134]\b", s.llm_model)
    body["max_completion_tokens" if gpt else "max_tokens"] = s.llm_max_tokens
    if s.llm_reasoning in ("off", "low", "high"):
        if gpt:
            # gpt-5 calls "off" minimal; gpt-5.1 and later call it none and refuse minimal.
            off = "none" if re.search(r"gpt-5\.\d", s.llm_model) else "minimal"
            body["reasoning_effort"] = {"off": off}.get(s.llm_reasoning, s.llm_reasoning)
        else:
            body["chat_template_kwargs"] = {"enable_thinking": s.llm_reasoning != "off"}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    return body


def _once(s, body, on_text):
    started, text, calls, finish = time.monotonic(), "", {}, None
    think = ThinkFilter()
    try:
        with _request(s, "/chat/completions", body) as resp:
            for event in _events(resp):
                if event.get("error"):
                    raise LLMError(f"The model server reported an error: {str(event['error'])[:300]}")
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        text += piece
                        shown = think.feed(piece)
                        if shown:
                            on_text(shown)
                    _merge_calls(calls, delta.get("tool_calls"))
                    finish = choice.get("finish_reason") or finish
    except (TimeoutError, OSError) as e:
        said = f"The model server stopped answering after {time.monotonic() - started:.0f} seconds."
        if text:  # part of an answer is already on screen: a retry would repeat it
            raise LLMError(said) from None
        raise Busy(said) from None
    except json.JSONDecodeError:
        raise LLMError("The model server sent a reply that is not valid JSON. Is LLM_URL the API address?") from None
    tail = think.flush()
    if tail:
        on_text(tail)
    message = {"role": "assistant", "content": strip_think(text)}
    if calls:
        message["tool_calls"] = [
            {"id": c["id"] or f"call_{i}", "type": "function",
             "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
            for i, c in sorted(calls.items())]
    return message, finish


def chat(s, messages, tools=None, on_text=lambda t: None, on_wait=lambda text: None, sleep=time.sleep):
    """One model turn, waiting out a busy server. Returns (assistant message, finish reason)."""
    body, waited, delay = _body(s, messages, tools), 0, 5
    while True:
        try:
            return _once(s, body, on_text)
        except Busy as e:
            wait = int(e.retry_after) if str(e.retry_after or "").isdigit() else delay
            if waited + wait > s.llm_busy_wait:
                raise LLMError(f"{e} Gave up after waiting {waited} seconds.") from None
            on_wait(f"{e} Trying again in {wait} seconds.")
            sleep(wait)
            waited, delay = waited + wait, delay * 2
