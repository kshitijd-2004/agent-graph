"""
per-test stdout/stderr capturing mechanism.

"""
import collections
import contextlib
import io
import os
import sys
from io import UnsupportedOperation
from tempfile import TemporaryFile
from typing import Generator
from typing import Optional
from typing import TextIO
from typing import Tuple
from typing import Union
import pytest
from _pytest.compat import TYPE_CHECKING
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.fixtures import SubRequest
from _pytest.nodes import Collector
from _pytest.nodes import Item
if TYPE_CHECKING:
    from typing_extensions import Literal

    _CaptureMethod = Literal["fd", "sys", "no", "tee-sys"]
class CaptureIO(io.TextIOWrapper):
    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="UTF-8", newline="", write_through=True)

    def getvalue(self) -> str:
        assert isinstance(self.buffer, io.BytesIO)
        return self.buffer.getvalue().decode("UTF-8")
class TeeCaptureIO(CaptureIO):
    def __init__(self, other: TextIO) -> None:
        self._other = other
        super().__init__()

    def write(self, s: str) -> int:
        super().write(s)
        return self._other.write(s)
class SysCaptureBinary:

    EMPTY_BUFFER = b""

    def __init__(self, fd: int, tmpfile=None, *, tee: bool = False) -> None:
        name = patchsysdict[fd]
        self._old = getattr(sys, name)
        self.name = name
        if tmpfile is None:
            if name == "stdin":
                tmpfile = DontReadFromInput()
            else:
                tmpfile = CaptureIO() if not tee else TeeCaptureIO(self._old)
        self.tmpfile = tmpfile
        self._state = "initialized"

    def repr(self, class_name: str) -> str:
        return "<{} {} _old={} _state={!r} tmpfile={!r}>".format(
            class_name,
            self.name,
            hasattr(self, "_old") and repr(self._old) or "<UNSET>",
            self._state,
            self.tmpfile,
        )

    def __repr__(self) -> str:
        return "<{} {} _old={} _state={!r} tmpfile={!r}>".format(
            self.__class__.__name__,
            self.name,
            hasattr(self, "_old") and repr(self._old) or "<UNSET>",
            self._state,
            self.tmpfile,
        )

class FDCaptureBinary:
    """Capture IO to/from a given os-level filedescriptor.

    snap() produces `bytes`
    """

    EMPTY_BUFFER = b""

    def __init__(self, targetfd: int) -> None:
        self.targetfd = targetfd

        try:
            os.fstat(targetfd)
        except OSError:
            # FD capturing is conceptually simple -- create a temporary file,
            # redirect the FD to it, redirect back when done. But when the
            # target FD is invalid it throws a wrench into this loveley scheme.
            #
            # Tests themselves shouldn't care if the FD is valid, FD capturing
            # should work regardless of external circumstances. So falling back
            # to just sys capturing is not a good option.
            #
            # Further complications are the need to support suspend() and the
            # possibility of FD reuse (e.g. the tmpfile getting the very same
            # target FD). The following approach is robust, I believe.
            self.targetfd_invalid = os.open(
                os.devnull, os.O_RDWR
            )  # type: Optional[int]
            os.dup2(self.targetfd_invalid, targetfd)
        else:
            self.targetfd_invalid = None
        self.targetfd_save = os.dup(targetfd)

        if targetfd == 0:
            self.tmpfile = open(os.devnull)
            self.syscapture = SysCapture(targetfd)
        else:
            self.tmpfile = EncodedFile(
                # TODO: Remove type ignore, fixed in next mypy release.
                TemporaryFile(buffering=0),  # type: ignore[arg-type]
                encoding="utf-8",
                errors="replace",
                write_through=True,
            )
            if targetfd in patchsysdict:
                self.syscapture = SysCapture(targetfd, self.tmpfile)
            else:
                self.syscapture = NoCapture()

        self._state = "initialized"

    def __repr__(self) -> str:
        return "<{} {} oldfd={} _state={!r} tmpfile={!r}>".format(
            self.__class__.__name__,
            self.targetfd,
            self.targetfd_save,
            self._state,
            self.tmpfile,
        )

    def _assert_state(self, op: str, states: Tuple[str, ...]) -> None:
        assert (
            self._state in states
        ), "cannot {} in state {!r}: expected one of {}".format(
            op, self._state, ", ".join(states)
        )

class MultiCapture:
    _state = None
    _in_suspended = False

    def __init__(self, in_, out, err) -> None:
        self.in_ = in_
        self.out = out
        self.err = err

    def __repr__(self) -> str:
        return "<MultiCapture out={!r} err={!r} in_={!r} _state={!r} _in_suspended={!r}>".format(
            self.out, self.err, self.in_, self._state, self._in_suspended,
        )

    def start_capturing(self) -> None:
        self._state = "started"
        if self.in_:
            self.in_.start()
        if self.out:
            self.out.start()
        if self.err:
            self.err.start()

class CaptureManager:
    """
    Capture plugin, manages that the appropriate capture method is enabled/disabled during collection and each
    test phase (setup, call, teardown). After each of those points, the captured output is obtained and
    attached to the collection/runtest report.

    There are two levels of capture:
    * global: which is enabled by default and can be suppressed by the ``-s`` option. This is always enabled/disabled
      during collection and each test phase.
    * fixture: when a test function or one of its fixture depend on the ``capsys`` or ``capfd`` fixtures. In this
      case special handling is needed to ensure the fixtures take precedence over the global capture.
    """

    def __init__(self, method: "_CaptureMethod") -> None:
        self._method = method
        self._global_capturing = None  # type: Optional[MultiCapture]
        self._capture_fixture = None  # type: Optional[CaptureFixture]

    def __repr__(self) -> str:
        return "<CaptureManager _method={!r} _global_capturing={!r} _capture_fixture={!r}>".format(
            self._method, self._global_capturing, self._capture_fixture
        )

    def is_capturing(self) -> Union[str, bool]:
        if self.is_globally_capturing():
            return "global"
        if self._capture_fixture:
            return "fixture %s" % self._capture_fixture.request.fixturename
        return False

    # Global capturing control

    def is_globally_capturing(self) -> bool:
        return self._method != "no"

class CaptureFixture:
    """
    Object returned by :py:func:`capsys`, :py:func:`capsysbinary`, :py:func:`capfd` and :py:func:`capfdbinary`
    fixtures.
    """

    def __init__(self, captureclass, request: SubRequest) -> None:
        self.captureclass = captureclass
        self.request = request
        self._capture = None  # type: Optional[MultiCapture]
        self._captured_out = self.captureclass.EMPTY_BUFFER
        self._captured_err = self.captureclass.EMPTY_BUFFER

    def _start(self) -> None:
        if self._capture is None:
            self._capture = MultiCapture(
                in_=None, out=self.captureclass(1), err=self.captureclass(2),
            )
            self._capture.start_capturing()

    def close(self) -> None:
        if self._capture is not None:
            out, err = self._capture.pop_outerr_to_orig()
            self._captured_out += out
            self._captured_err += err
            self._capture.stop_capturing()
            self._capture = None

