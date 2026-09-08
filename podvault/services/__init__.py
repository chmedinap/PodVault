"""
Business-logic layer shared by the CLI and (upcoming) web interface.

Each function here returns a plain result object instead of printing to
stdout or returning bare bools/tuples, so callers other than the CLI
(a web request handler, a background job) can render the outcome however
they need to.
"""

from podvault.services.results import (
    AppSettings,
    CheckOutcome,
    CheckRunResult,
    DiskUsage,
    DownloadResult,
    EpisodeDetail,
    EpisodeInfo,
    EpisodeListResult,
    FeedResult,
    FileOnDisk,
    MigrationMove,
    MigrationResult,
    MissingEpisode,
    NamingPreview,
    PodcastInfo,
    PodcastListResult,
    PlaybackInfo,
    PodcastVerifyResult,
    ProcessFeedsResult,
    RenameResult,
    ScheduleInfo,
    ScheduleResult,
    SettingsResult,
    StatusResult,
    VerifyResult,
)
from podvault.services.feeds import add_feed, process_feeds_file, process_single_feed
from podvault.services.podcasts import get_podcast_info, get_status, list_podcasts
from podvault.services.downloads import download_all, download_episode, download_last, preview_pending
from podvault.services.migration import migrate_layout
from podvault.services.verification import verify_files
from podvault.services.episodes import get_episode, list_episodes, rename_episode
from podvault.services.settings import get_settings, preview, update_settings
from podvault.services.scheduling import get_podcast_schedule, list_schedules, run_due_checks, set_podcast_schedule
from podvault.services.playback import get_playback_info
from podvault.services.queue import QueueEstimate, estimate_progress
from podvault.services.storage import get_disk_usage, list_downloaded_episodes, list_local_only_episodes, run_id3_check, run_id3_retag
from podvault.services.integrity import list_corrupt_episodes, run_integrity_check
from podvault.services.export import export_library_manifest, export_podcast_manifest

__all__ = [
    'AppSettings',
    'CheckOutcome',
    'CheckRunResult',
    'DiskUsage',
    'DownloadResult',
    'EpisodeDetail',
    'EpisodeInfo',
    'EpisodeListResult',
    'FeedResult',
    'FileOnDisk',
    'MigrationMove',
    'MigrationResult',
    'MissingEpisode',
    'NamingPreview',
    'PodcastInfo',
    'PodcastListResult',
    'PlaybackInfo',
    'PodcastVerifyResult',
    'ProcessFeedsResult',
    'RenameResult',
    'ScheduleInfo',
    'ScheduleResult',
    'SettingsResult',
    'StatusResult',
    'VerifyResult',
    'add_feed',
    'process_feeds_file',
    'process_single_feed',
    'get_podcast_info',
    'get_status',
    'list_podcasts',
    'download_all',
    'download_episode',
    'download_last',
    'preview_pending',
    'migrate_layout',
    'verify_files',
    'get_episode',
    'list_episodes',
    'rename_episode',
    'get_settings',
    'preview',
    'update_settings',
    'get_podcast_schedule',
    'list_schedules',
    'run_due_checks',
    'set_podcast_schedule',
    'get_playback_info',
    'QueueEstimate',
    'estimate_progress',
    'get_disk_usage',
    'list_downloaded_episodes',
    'list_local_only_episodes',
    'run_id3_check',
    'run_id3_retag',
    'run_integrity_check',
    'list_corrupt_episodes',
    'export_library_manifest',
    'export_podcast_manifest',
]
