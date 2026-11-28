"""Entry point for the frozen back end (PyInstaller)."""
import multiprocessing
import sys
import traceback


def _log_file():
    from greplens.config import data_dir
    return open(data_dir() / "logs" / "backend.log", "a", encoding="utf-8", buffering=1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if getattr(sys, "frozen", False) or sys.stdout is None:  # installed app: keep output in the data folder's logs
        try:
            log = _log_file()
            sys.stdout = sys.stderr = log
            import faulthandler
            faulthandler.enable(file=log)  # a crash inside Windows or a library is written to the log too
            import datetime
            log.write(f"\n=== GrepLens back end started {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
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
