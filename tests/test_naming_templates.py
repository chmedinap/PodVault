"""
Tests for the naming template engine (`download/utils.py`), introduced in
ROADMAP.md Fase 7 to let folder/filename layout be configured instead of
hardcoded.
"""

import pandas as pd
import pytest

from podvault import config
from podvault.download.utils import (
    build_episode_base_filename,
    build_episode_folder_path,
    episode_template_variables,
    render_naming_template,
)

_DATE = pd.Timestamp('2024-03-07')


def test_render_naming_template_basic_substitution():
    result = render_naming_template("{podcast_name}/{year}", {"podcast_name": "my_show", "year": 2024})
    assert result == "my_show/2024"


def test_render_naming_template_rejects_unknown_variable():
    with pytest.raises(ValueError):
        render_naming_template("{not_a_real_variable}", {"year": 2024})


def test_render_naming_template_rejects_bad_format_spec():
    with pytest.raises(ValueError):
        render_naming_template("{episode_number:03d}", {"episode_number": ""})


def test_episode_template_variables_with_number():
    variables = episode_template_variables("my_show", _DATE, 7, 2, "Sample Title")
    assert variables["episode_label"] == "007"
    assert variables["episode_number"] == 7
    assert variables["season_number"] == 2
    assert variables["year"] == 2024
    assert variables["month"] == "03"
    assert variables["day"] == "07"
    assert variables["date"] == "2024-03-07"


def test_episode_template_variables_without_number_falls_back_to_date():
    variables = episode_template_variables("my_show", _DATE, None, None, "Sample Title")
    assert variables["episode_label"] == "2024-03-07"
    assert variables["episode_number"] == ""
    assert variables["season_number"] == ""


def test_build_episode_folder_path_default_template_matches_legacy_layout():
    path = build_episode_folder_path("downloads", "my_show", _DATE, 7, 2, "Sample Title")
    assert str(path) == "downloads/my_show/2024/03/007 - Sample Title"


def test_build_episode_folder_path_default_template_falls_back_without_number():
    path = build_episode_folder_path("downloads", "my_show", _DATE, None, None, "Sample Title")
    assert str(path) == "downloads/my_show/2024/03/2024-03-07 - Sample Title"


def test_build_episode_folder_path_custom_template():
    path = build_episode_folder_path(
        "downloads", "my_show", _DATE, 7, 2, "Sample Title",
        folder_template="{podcast_name}/S{season_number}/E{episode_number}",
    )
    assert str(path) == "downloads/my_show/S2/E7"


def test_build_episode_folder_path_drops_path_traversal_segments():
    path = build_episode_folder_path(
        "downloads", "my_show", _DATE, 7, 2, "Sample Title",
        folder_template="../../etc/{podcast_name}",
    )
    # '..' segments are dropped, so the result never escapes `downloads_folder`
    assert str(path) == "downloads/etc/my_show"
    assert "downloads" in path.parts
    assert ".." not in path.parts


def test_build_episode_folder_path_rejects_unknown_variable():
    with pytest.raises(ValueError):
        build_episode_folder_path(
            "downloads", "my_show", _DATE, 7, 2, "Sample Title",
            folder_template="{not_a_real_variable}",
        )


def test_build_episode_base_filename_default_template():
    filename = build_episode_base_filename("my_show", _DATE, 7, 2, "Sample Title")
    assert filename == "Sample Title - my_show"


def test_build_episode_base_filename_custom_template():
    filename = build_episode_base_filename(
        "my_show", _DATE, 7, 2, "Sample Title",
        filename_template="{episode_label} {episode_title}",
    )
    assert filename == "007 Sample Title"


def test_build_episode_base_filename_sanitizes_result():
    filename = build_episode_base_filename(
        "my_show", _DATE, 7, 2, "Sample Title",
        filename_template="{episode_title}/weird:chars*here",
    )
    assert "/" not in filename
    assert ":" not in filename
    assert "*" not in filename
