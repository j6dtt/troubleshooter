"""Run one program with fixed arguments. Never through a shell."""
import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    ok: bool
    text: str


def run(argv, timeout=30, parsed=False):
    """parsed=True: the caller parses the output, so warnings on stderr stay out of it.
    kubectl prints some after its JSON, e.g. when the cluster's version is far from its own.
    If the program fails, stderr is still returned: that's the error message."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE if parsed else subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(False, f"{argv[0]} gave no answer within {timeout} seconds.")
    except FileNotFoundError:
        return Result(False, f"{argv[0]} is not installed here.")
    text = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode:
        if parsed:
            text = "\n".join(x for x in (text, p.stderr.decode("utf-8", "replace").strip()) if x)
        return Result(False, text or f"{argv[0]} failed with exit code {p.returncode}.")
    return Result(True, text)
