from .shared import ensure_dirs, setup_logging
from .settings import load_settings
from .engine import run_scan


def main() -> int:
    ensure_dirs()
    setup_logging()
    settings = load_settings()
    return run_scan(settings, pause_on_exit=True)


if __name__ == "__main__":
    raise SystemExit(main())
