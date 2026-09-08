"""
Service for the naming templates configurable from the Settings screen
(see ROADMAP.md Fase 7).

A candidate template is rendered against two sample episodes before being
saved — one with an episode/season number, one without — so a template
that only works when those are present gets rejected up front instead of
failing later on whichever real episode happens to lack one.
"""

from typing import Optional

import pandas as pd

from podvault import config
from podvault.database.connection import get_db_connection
from podvault.database.settings import get_app_settings, update_app_settings
from podvault.download.utils import episode_template_variables, render_naming_template, sanitize_filename
from podvault.services.results import AppSettings, NamingPreview, SettingsResult

_SAMPLE_DATE = pd.Timestamp('2024-03-07')
_SAMPLE_TITLE = 'Sample Episode Title'
_SAMPLE_PODCAST = 'sample_podcast'


def _render_folder(folder_template: str, variables: dict) -> str:
    """Render `folder_template` and re-join sanitized segments, mirroring build_episode_folder_path()."""
    rendered = render_naming_template(folder_template, variables)
    segments = [sanitize_filename(seg) for seg in rendered.split('/') if seg.strip() not in ('', '.', '..')]
    if not segments:
        raise ValueError("folder template must not render to an empty path")
    return '/'.join(segments)


def _render_filename(filename_template: str, variables: dict) -> str:
    rendered = sanitize_filename(render_naming_template(filename_template, variables))
    if not rendered.strip():
        raise ValueError("filename template must not render to an empty name")
    return rendered


def preview(folder_template: str, filename_template: str) -> NamingPreview:
    """Render both templates against one sample episode, for a live preview before saving."""
    variables = episode_template_variables(_SAMPLE_PODCAST, _SAMPLE_DATE, 7, 2, _SAMPLE_TITLE)
    try:
        folder_path = _render_folder(folder_template, variables)
    except ValueError as e:
        return NamingPreview(error=str(e))

    try:
        filename = _render_filename(filename_template, variables)
    except ValueError as e:
        return NamingPreview(folder_path=folder_path, error=str(e))

    return NamingPreview(folder_path=folder_path, filename=filename)


def _validate(folder_template: str, filename_template: str) -> Optional[str]:
    """Return an error message, or None if both templates are safe to save."""
    if not folder_template.strip():
        return "Folder template can't be empty"
    if not filename_template.strip():
        return "Filename template can't be empty"

    # A numbered sample and a number-less sample: a template that only works
    # for one of them (e.g. "{episode_number:03d}" with no fallback) is rejected here.
    samples = [
        episode_template_variables(_SAMPLE_PODCAST, _SAMPLE_DATE, 7, 2, _SAMPLE_TITLE),
        episode_template_variables(_SAMPLE_PODCAST, _SAMPLE_DATE, None, None, _SAMPLE_TITLE),
    ]
    for variables in samples:
        try:
            _render_folder(folder_template, variables)
            _render_filename(filename_template, variables)
        except ValueError as e:
            return str(e)
    return None


def get_settings() -> AppSettings:
    """Return the currently active naming templates."""
    with get_db_connection(config.DB_PATH) as conn:
        settings = get_app_settings(conn)
    return AppSettings(folder_template=settings['folder_template'], filename_template=settings['filename_template'])


def update_settings(folder_template: str, filename_template: str) -> SettingsResult:
    """Validate and persist new naming templates."""
    folder_template = (folder_template or '').strip()
    filename_template = (filename_template or '').strip()

    error = _validate(folder_template, filename_template)
    if error:
        return SettingsResult(success=False, error=error)

    with get_db_connection(config.DB_PATH) as conn:
        update_app_settings(conn, folder_template, filename_template)

    return SettingsResult(success=True, folder_template=folder_template, filename_template=filename_template)
