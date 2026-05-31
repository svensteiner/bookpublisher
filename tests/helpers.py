import tempfile
from pathlib import Path
from uuid import uuid4


def runtime_dir(name: str) -> Path:
    """Create a unique, throwaway workspace for a test.

    Lives under the OS temp directory (not the repo) so test runs never
    pollute the project's ``artifacts/`` folder. The directory is created
    fresh and returned; the OS reclaims temp space, so no explicit
    teardown is required.
    """

    root = (
        Path(tempfile.gettempdir())
        / "bookpublisher_test_runtime"
        / f"{name}_{uuid4().hex}"
    )
    root.mkdir(parents=True, exist_ok=False)
    return root
