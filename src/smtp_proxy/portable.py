import errno
from contextlib import contextmanager

from smtp_proxy import file_lock


class RuntimeActiveError(RuntimeError):
    pass


@contextmanager
def idle_runtime(directory):
    with (directory / "runtime.lock").open("a+b") as stream:
        stream.seek(0)
        try:
            file_lock.acquire(stream)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EDEADLK, errno.EAGAIN}:
                raise RuntimeActiveError("CLI zuerst beenden.") from None
            raise
        try:
            yield
        finally:
            stream.seek(0)
            file_lock.release(stream)


def runtime_status(directory):
    if not (directory / "runtime.lock").exists():
        return "stopped"
    try:
        with idle_runtime(directory):
            return "stopped"
    except RuntimeActiveError:
        return "running"