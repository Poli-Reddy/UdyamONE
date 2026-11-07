"""All tests use disposable stores; never write into the user's orders.db."""
import os
import tempfile

_TEST_DIR = tempfile.TemporaryDirectory(prefix="udyamone-tests-")
os.environ["UDYAM_DB"] = os.path.join(_TEST_DIR.name, "orders.db")
os.environ["UDYAM_CONSENT_LOG"] = os.path.join(_TEST_DIR.name, "consent.jsonl")
os.environ["UDYAM_DEMO"] = "true"
os.environ["UDYAM_TODAY"] = "2026-07-15"


def pytest_configure(config):
    if not config.option.basetemp:
        config.option.basetemp = os.path.join(_TEST_DIR.name, "pytest")


def pytest_sessionfinish(session, exitstatus):
    try:
        import db
        db.dispose()
    except Exception:
        pass
    _TEST_DIR.cleanup()
