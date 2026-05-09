from modules.discovery import _extract_metadata_from_text, discover_books
from tests.helpers import runtime_dir


SUPPORTED = {
    "manuscripts": [".docx"],
    "text": [".md", ".txt"],
    "covers": [".jpg", ".png", ".jpeg"],
}


def test_folder_discovery_top_level():
    workspace = runtime_dir("discovery")
    manuscript = workspace / "Unter_Fuenfzig_Euro_KDP_Endversion_KORRIGIERT.docx"
    cover = workspace / "Cover_KDP_Final.jpg"
    manuscript.write_bytes(b"fake-docx")
    cover.write_bytes(b"fake-jpg")

    projects = discover_books(workspace, {"nicht_hochladen"}, SUPPORTED)

    assert len(projects) == 1
    assert projects[0].manuscript == manuscript
    assert projects[0].cover == cover


def test_empty_project_handling():
    workspace = runtime_dir("empty")
    projects = discover_books(workspace, set(), SUPPORTED)
    assert projects == []


def test_author_cleanup_from_copyright_line():
    metadata = _extract_metadata_from_text(
        "Unter Fuenfzig Euro\n"
        "Wie ich ein Unternehmen ohne Mitarbeiter aufgebaut habe\n"
        "Copyright © 2026 Mag. Sven Steiner. Alle Rechte vorbehalten."
    )

    assert metadata["author"] == "Mag. Sven Steiner"


def test_supplemental_metadata_can_be_read_from_skipped_editorial_folder():
    workspace = runtime_dir("supplemental")
    manuscript = workspace / "Unter_Fuenfzig_Euro_KDP_Endversion_KORRIGIERT.docx"
    manuscript.write_bytes(b"fake-docx")
    hidden = workspace / "nicht_hochladen"
    hidden.mkdir()
    metadata = hidden / "Amazon_Beschreibung_und_Metadaten.md"
    metadata.write_text(
        "## KDP Titel\n"
        "**Unter Fuenfzig Euro**\n\n"
        "## KDP Untertitel\n"
        "**Wie man KI operativ einsetzt**\n\n"
        "## Amazon Beschreibung\n"
        "Eine nuechterne Beschreibung.",
        encoding="utf-8",
    )

    projects = discover_books(workspace, {"nicht_hochladen"}, SUPPORTED, {"nicht_hochladen"})

    assert len(projects) == 1
    assert projects[0].title == "Unter Fuenfzig Euro"
    assert projects[0].subtitle == "Wie man KI operativ einsetzt"
    assert projects[0].amazon_description == "Eine nuechterne Beschreibung."
    assert metadata in projects[0].metadata_files
