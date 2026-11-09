import errno
import msvcrt
from contextlib import contextmanager


class RuntimeActiveError(RuntimeError):
    pass


@contextmanager
def idle_runtime(directory):
    with (directory / "runtime.lock").open("a+b") as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EDEADLK}:
                raise RuntimeActiveError("CLI zuerst beenden.") from None
            raise
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def runtime_status(directory):
    if not (directory / "runtime.lock").exists():
        return "stopped"
    try:
        with idle_runtime(directory):
            return "stopped"
    except RuntimeActiveError:
        return "running"