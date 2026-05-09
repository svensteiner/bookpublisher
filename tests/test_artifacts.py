from modules.artifacts import ArtifactWriter
from modules.run_logger import RunLogger
from tests.helpers import runtime_dir


def test_artifact_creation():
    workspace = runtime_dir("artifacts")
    logger = RunLogger(workspace / "logs")
    writer = ArtifactWriter(workspace / "artifacts", logger)

    path = writer.write_text("discovery_report.md", "# Report")

    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("# Report")


def test_no_overwrite_of_source_files():
    workspace = runtime_dir("no_overwrite")
    source = workspace / "source.docx"
    source.write_text("original", encoding="utf-8")
    logger = RunLogger(workspace / "logs")
    writer = ArtifactWriter(workspace / "artifacts", logger)

    writer.write_text("manuscript_review.md", "review")

    assert source.read_text(encoding="utf-8") == "original"
