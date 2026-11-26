"""Entry point for the frozen back end (PyInstaller)."""
import multiprocessing
import sys
import traceback


def _log_file():
    from greplens.config import data_dir
    return open(data_dir() / "logs" / "backend.log", "a", encoding="utf-8", buffering=1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.stdout is None or sys.stderr is None:  # windowed build: keep output in the data folder's logs
        try:
            sys.stdout = sys.stderr = _log_file()
        except Exception:  # noqa: BLE001
            pass
    try:
        from greplens.main import run
        run(open_browser="--browser" in sys.argv)
    except BaseException:
        try:
            _log_file().write(traceback.format_exc())
        finally:
            raise
