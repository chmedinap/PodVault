"""Tests for `services.settings` (the Settings screen's backend)."""

from podvault import config, services


def test_get_settings_returns_defaults_on_fresh_db(isolated_env):
    settings = services.get_settings()
    assert settings.folder_template == config.DEFAULT_FOLDER_TEMPLATE
    assert settings.filename_template == config.DEFAULT_FILENAME_TEMPLATE


def test_update_settings_persists_and_is_read_back(isolated_env):
    result = services.update_settings(
        folder_template="{podcast_name}/{year}",
        filename_template="{episode_title}",
    )
    assert result.success
    assert result.error is None

    settings = services.get_settings()
    assert settings.folder_template == "{podcast_name}/{year}"
    assert settings.filename_template == "{episode_title}"


def test_update_settings_rejects_empty_folder_template(isolated_env):
    result = services.update_settings(folder_template="", filename_template="{episode_title}")
    assert not result.success
    assert result.error


def test_update_settings_rejects_unknown_variable(isolated_env):
    result = services.update_settings(
        folder_template="{podcast_name}/{not_a_real_variable}",
        filename_template="{episode_title}",
    )
    assert not result.success


def test_update_settings_rejects_template_that_only_works_with_a_number(isolated_env):
    # Fails for the number-less sample: '' isn't valid for a :03d format spec.
    result = services.update_settings(
        folder_template="{podcast_name}",
        filename_template="{episode_number:03d} - {episode_title}",
    )
    assert not result.success


def test_update_settings_does_not_persist_on_validation_failure(isolated_env):
    before = services.get_settings()
    services.update_settings(folder_template="{bad_variable}", filename_template="{episode_title}")
    after = services.get_settings()
    assert after.folder_template == before.folder_template


def test_preview_does_not_persist(isolated_env):
    before = services.get_settings()
    preview = services.preview(folder_template="{podcast_name}/{year}", filename_template="{episode_title}")
    assert preview.success
    assert preview.folder_path == "sample_podcast/2024"
    assert preview.filename == "Sample Episode Title"

    after = services.get_settings()
    assert after.folder_template == before.folder_template


def test_preview_reports_error_for_broken_template(isolated_env):
    preview = services.preview(folder_template="{nope}", filename_template="{episode_title}")
    assert not preview.success
    assert preview.error
