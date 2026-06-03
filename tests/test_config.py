from modules.config import load_config
from tests.helpers import runtime_dir


def test_config_loading():
    workspace = runtime_dir("config")
    config = workspace / "config.yaml"
    config.write_text(
        """
default_input_path: "C:\\\\Books"
default_model: gpt-4.1
fallback_model: gpt-4.1-mini
skip_directories: [artifacts]
supported_files:
  manuscripts: [.docx]
supplemental_text_directories: [nicht_hochladen]
skills_directory: skills
memory_path: artifacts/agent_memory.json
""",
        encoding="utf-8",
    )

    loaded = load_config(config)

    assert loaded.default_model == "gpt-4.1"
    assert loaded.fallback_model == "gpt-4.1-mini"
    assert "artifacts" in loaded.skip_directories
    assert "nicht_hochladen" in loaded.supplemental_text_directories
    assert str(loaded.skills_directory) == "skills"
    assert str(loaded.memory_path).replace("\\", "/") == "artifacts/agent_memory.json"


def _minimal_config(workspace_name: str, extra: str = "") -> object:
    workspace = runtime_dir(workspace_name)
    config = workspace / "config.yaml"
    config.write_text(
        "default_model: claude-sonnet-4-6\n"
        "fallback_model: claude-haiku-4-5-20251001\n"
        + extra,
        encoding="utf-8",
    )
    return load_config(config)


def test_positioning_limit_defaults_to_one():
    loaded = _minimal_config("config_positioning_default")
    assert loaded.beginner_summary_positioning_limit == 1


def test_positioning_limit_reads_yaml_value():
    loaded = _minimal_config(
        "config_positioning_yaml",
        extra="beginner_summary_positioning_limit: 3\n",
    )
    assert loaded.beginner_summary_positioning_limit == 3


def test_positioning_limit_clamps_above_three():
    """Values above 3 must clamp to the TOP_POSITIONING_MAX_LIMIT so the
    summary never gets crowded with low-strength signals."""
    loaded = _minimal_config(
        "config_positioning_high",
        extra="beginner_summary_positioning_limit: 12\n",
    )
    assert loaded.beginner_summary_positioning_limit == 3


def test_positioning_limit_clamps_below_one():
    """Values <1 must clamp to 1 so the strongest angle is always shown
    when a real positioning signal exists."""
    loaded = _minimal_config(
        "config_positioning_low",
        extra="beginner_summary_positioning_limit: 0\n",
    )
    assert loaded.beginner_summary_positioning_limit == 1


def test_kdp_keyword_limit_defaults_to_three():
    loaded = _minimal_config("config_kdp_keyword_default")
    assert loaded.beginner_summary_kdp_keyword_limit == 3


def test_kdp_keyword_limit_reads_yaml_value():
    loaded = _minimal_config(
        "config_kdp_keyword_yaml",
        extra="beginner_summary_kdp_keyword_limit: 5\n",
    )
    assert loaded.beginner_summary_kdp_keyword_limit == 5


def test_kdp_keyword_limit_clamps_above_seven():
    """KDP allows at most 7 keywords — anything higher would imply slots
    that Amazon never accepts, so the loader caps at 7."""
    loaded = _minimal_config(
        "config_kdp_keyword_high",
        extra="beginner_summary_kdp_keyword_limit: 42\n",
    )
    assert loaded.beginner_summary_kdp_keyword_limit == 7


def test_kdp_keyword_limit_clamps_below_one():
    """Values <1 must clamp to 1 so the strongest slot is always shown
    when a real keyword report exists — silencing the section entirely
    would hide the most actionable KDP backend block from the summary."""
    loaded = _minimal_config(
        "config_kdp_keyword_low",
        extra="beginner_summary_kdp_keyword_limit: 0\n",
    )
    assert loaded.beginner_summary_kdp_keyword_limit == 1


def test_kdp_keywords_llm_defaults_to_false():
    loaded = _minimal_config("config_kdp_kw_llm_default")
    assert loaded.kdp_keywords_llm_enabled is False


def test_kdp_keywords_llm_reads_yaml_true():
    loaded = _minimal_config(
        "config_kdp_kw_llm_true",
        extra="kdp_keywords_llm_enabled: true\n",
    )
    assert loaded.kdp_keywords_llm_enabled is True


def test_weakest_limit_defaults_to_three():
    loaded = _minimal_config("config_weakest_default")
    assert loaded.beginner_summary_weakest_limit == 3


def test_weakest_limit_reads_yaml_value():
    loaded = _minimal_config(
        "config_weakest_yaml",
        extra="beginner_summary_weakest_limit: 5\n",
    )
    assert loaded.beginner_summary_weakest_limit == 5


def test_weakest_limit_clamps_above_ten():
    """Beyond 10 the section stops being a 'weakest' signal and turns into
    the full chapter report — the loader caps at 10 so the summary stays
    focused on the top fix-candidates."""
    loaded = _minimal_config(
        "config_weakest_high",
        extra="beginner_summary_weakest_limit: 99\n",
    )
    assert loaded.beginner_summary_weakest_limit == 10


def test_weakest_limit_clamps_below_one():
    """Values <1 must clamp to 1 so the strongest fix-candidate is always
    surfaced when a real chapter report exists — silencing the section
    entirely would hide the most actionable diagnostic in the summary."""
    loaded = _minimal_config(
        "config_weakest_low",
        extra="beginner_summary_weakest_limit: 0\n",
    )
    assert loaded.beginner_summary_weakest_limit == 1


def test_weakest_sample_limit_defaults_to_one():
    loaded = _minimal_config("config_weakest_sample_default")
    assert loaded.beginner_summary_weakest_sample_limit == 1


def test_weakest_sample_limit_reads_yaml_value():
    loaded = _minimal_config(
        "config_weakest_sample_yaml",
        extra="beginner_summary_weakest_sample_limit: 3\n",
    )
    assert loaded.beginner_summary_weakest_sample_limit == 3


def test_weakest_sample_limit_clamps_above_ten():
    """Beyond 10 the section stops being a 'weakest' signal and turns into
    the full sample-scan report — the loader caps at 10 so the summary
    stays focused on the top drop-off risks."""
    loaded = _minimal_config(
        "config_weakest_sample_high",
        extra="beginner_summary_weakest_sample_limit: 99\n",
    )
    assert loaded.beginner_summary_weakest_sample_limit == 10


def test_weakest_sample_limit_clamps_below_one():
    """Values <1 must clamp to 1 so the highest-risk Kindle-Sample section
    is always surfaced when one exists — silencing the section entirely
    would hide the single most actionable diagnostic for sample drop-off."""
    loaded = _minimal_config(
        "config_weakest_sample_low",
        extra="beginner_summary_weakest_sample_limit: 0\n",
    )
    assert loaded.beginner_summary_weakest_sample_limit == 1


def test_readability_target_band_defaults_to_50_80():
    """Default Amstad band matches populaeres deutsches Sachbuch (B1/B2)."""
    loaded = _minimal_config("config_readability_default")
    assert loaded.readability_target_min == 50
    assert loaded.readability_target_max == 80


def test_readability_target_band_reads_yaml_values():
    """Authors of Fachbuecher can lower the band so the QA gate stops
    flagging dense paragraphs as 'too hard'."""
    loaded = _minimal_config(
        "config_readability_fachbuch",
        extra=(
            "readability_target_min: 30\n"
            "readability_target_max: 55\n"
        ),
    )
    assert loaded.readability_target_min == 30
    assert loaded.readability_target_max == 55


def test_readability_target_band_lifestyle_book():
    """Authors of lifestyle nonfiction can raise the band so the gate
    flags passages that feel too academic for the audience."""
    loaded = _minimal_config(
        "config_readability_lifestyle",
        extra=(
            "readability_target_min: 65\n"
            "readability_target_max: 95\n"
        ),
    )
    assert loaded.readability_target_min == 65
    assert loaded.readability_target_max == 95


def test_readability_target_band_clamps_below_hard_min():
    """Values below the Amstad hard floor (10) clamp into the sane range
    so the QA gate keeps producing meaningful target hints."""
    loaded = _minimal_config(
        "config_readability_below_floor",
        extra=(
            "readability_target_min: -5\n"
            "readability_target_max: 25\n"
        ),
    )
    assert loaded.readability_target_min == 10
    assert loaded.readability_target_max == 25


def test_readability_target_band_clamps_above_hard_max():
    """Values above 100 clamp to 100 — the Amstad formula stops producing
    meaningful results above that ceiling for German text."""
    loaded = _minimal_config(
        "config_readability_above_ceiling",
        extra=(
            "readability_target_min: 70\n"
            "readability_target_max: 250\n"
        ),
    )
    assert loaded.readability_target_min == 70
    assert loaded.readability_target_max == 100


def test_readability_target_band_falls_back_when_degenerate():
    """When min >= max (typo or clamping collapse), the band falls back
    to the canonical 50/80 default instead of failing the run."""
    loaded = _minimal_config(
        "config_readability_degenerate",
        extra=(
            "readability_target_min: 90\n"
            "readability_target_max: 60\n"
        ),
    )
    assert loaded.readability_target_min == 50
    assert loaded.readability_target_max == 80


def test_readability_target_band_falls_back_when_equal():
    """A zero-width band (min == max) is degenerate — fall back to default."""
    loaded = _minimal_config(
        "config_readability_zero_width",
        extra=(
            "readability_target_min: 70\n"
            "readability_target_max: 70\n"
        ),
    )
    assert loaded.readability_target_min == 50
    assert loaded.readability_target_max == 80


def test_readability_target_band_falls_back_when_nonnumeric():
    """Non-numeric YAML values fall back to defaults rather than crashing
    the config loader — protects against typos like 'fifty'/'eighty'."""
    loaded = _minimal_config(
        "config_readability_nonnumeric",
        extra=(
            "readability_target_min: fifty\n"
            "readability_target_max: eighty\n"
        ),
    )
    assert loaded.readability_target_min == 50
    assert loaded.readability_target_max == 80
