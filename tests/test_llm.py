import threading
import time

import pytest

from tipitaka_qa.llm import invoke_within


class _Stalled:
    """A model call that never finishes, like a provider that only sends keep-alive bytes."""
    def __init__(self):
        self.release = threading.Event()

    def invoke(self, messages):
        self.release.wait()
        return "late"


class _Quick:
    def invoke(self, messages):
        return f"ok {len(messages)}"


def test_stalled_call_times_out():
    llm = _Stalled()
    t = time.time()
    with pytest.raises(TimeoutError):
        invoke_within(llm, [], 0.3)
    assert time.time() - t < 2
    llm.release.set()


def test_quick_call_returns():
    assert invoke_within(_Quick(), [1, 2], 5) == "ok 2"
