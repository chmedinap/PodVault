"""
Result types returned by the services layer.

Plain dataclasses instead of tuples/bools/None so both the CLI and (later)
the web layer can render the same information without re-deriving it from
side-effecting print() calls.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FeedResult:
    """Outcome of processing a single feed URL."""
    feed_url: str
    success: bool
    podcast_name: Optional[str] = None
    episodes_found: int = 0
    error: Optional[str] = None
    skipped: bool = False


@dataclass
class ProcessFeedsResult:
    """Outcome of processing a batch of feeds (feeds.txt or a single `add`)."""
    feeds: List[FeedResult] = field(default_factory=list)
    error: Optional[str] = None  # set for failures before any feed was processed (bad file path, empty file, ...)

    @property
    def success(self) -> bool:
        if self.error:
            return False
        return any(f.success for f in self.feeds)


@dataclass
class OpmlImportResult:
    """Outcome of importing an OPML file (Fase 24)."""
    found: int = 0                                     # outlines with an xmlUrl
    added: List[str] = field(default_factory=list)     # new URLs appended to the feeds file
    already_listed: int = 0                            # valid URLs that were already in the feeds file
    invalid: List[str] = field(default_factory=list)   # "title: reason" for rejected URLs
    feeds: List[FeedResult] = field(default_factory=list)  # sync results for the added feeds
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def synced(self) -> int:
        return sum(1 for f in self.feeds if f.success)

    def __str__(self) -> str:
        """Human-readable job message (JobRunner stores `str(result)` on success)."""
        if self.error:
            return self.error
        parts = [f"{len(self.added)} new podcast(s) added ({self.synced} synced)"]
        if self.already_listed:
            parts.append(f"{self.already_listed} already in the library")
        if self.invalid:
            parts.append(f"{len(self.invalid)} invalid URL(s) skipped")
        failed = len(self.feeds) - self.synced
        if failed:
            parts.append(f"{failed} failed to sync")
        return ", ".join(parts)


@dataclass
class PodcastInfo:
    name: str
    display_name: Optional[str] = None
    total_episodes: Optional[int] = None
    downloaded_episodes: Optional[int] = None
    missing_episodes: Optional[int] = None
    total_size_bytes: Optional[int] = None
    podcast_image_url: Optional[str] = None
    scheduled: bool = False
    corrupt_episodes: int = 0
    local_only_episodes: int = 0


@dataclass
class PodcastListResult:
    podcasts: List[PodcastInfo] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def total_downloaded(self) -> int:
        return sum(p.downloaded_episodes or 0 for p in self.podcasts)

    @property
    def total_episode_count(self) -> int:
        return sum(p.total_episodes or 0 for p in self.podcasts)

    @property
    def total_missing(self) -> int:
        return sum(p.missing_episodes or 0 for p in self.podcasts)

    @property
    def total_size_bytes(self) -> int:
        return sum(p.total_size_bytes or 0 for p in self.podcasts)

    @property
    def total_corrupt(self) -> int:
        return sum(p.corrupt_episodes or 0 for p in self.podcasts)

    @property
    def total_local_only(self) -> int:
        return sum(p.local_only_episodes or 0 for p in self.podcasts)


@dataclass
class StatusResult:
    pending_by_podcast: Dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class DownloadResult:
    success: bool
    downloaded: int = 0
    total: int = 0
    error: Optional[str] = None

    def __str__(self) -> str:
        """Human-readable job message (JobRunner stores `str(result)` as the job's `message` on success)."""
        if self.error:
            return self.error
        return f"{self.downloaded} of {self.total} episode(s) downloaded"


@dataclass
class MigrationMove:
    """Outcome for a single episode's folder during a layout migration."""
    podcast_name: str
    episode_id: str
    episode_title: str
    old_folder: str
    new_folder: str
    status: str  # 'planned' (dry run), 'moved', 'skipped', 'error'
    detail: Optional[str] = None


@dataclass
class MigrationResult:
    apply: bool
    moves: List[MigrationMove] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        if self.error:
            return False
        return not any(m.status == 'error' for m in self.moves)


@dataclass
class MissingEpisode:
    episode_id: str
    title: str
    saved_path: Optional[str]


@dataclass
class PodcastVerifyResult:
    podcast_name: str
    missing_episodes: List[MissingEpisode] = field(default_factory=list)


@dataclass
class VerifyResult:
    podcasts: List[PodcastVerifyResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def total_missing(self) -> int:
        return sum(len(p.missing_episodes) for p in self.podcasts)


@dataclass
class EpisodeInfo:
    episode_id: str
    title: str
    custom_title: Optional[str]
    status: str
    saved_path: Optional[str]
    episode_number: Optional[str] = None
    published: Optional[str] = None
    size_bytes: Optional[int] = None
    in_feed: bool = True


@dataclass
class EpisodeListResult:
    podcast_name: str
    episodes: List[EpisodeInfo] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class FileOnDisk:
    name: str
    size_bytes: int


@dataclass
class EpisodeDetail:
    """Everything the episode detail page needs: metadata, on-disk files, and
    cheap-to-compute-for-one-file extras (duration, ID3 completeness) that
    aren't worth precomputing for every episode in a list."""
    podcast_name: str
    episode_id: str
    title: str = ''
    display_title: str = ''
    custom_title: Optional[str] = None
    status: str = 'not downloaded'
    saved_path: Optional[str] = None
    published: Optional[str] = None
    episode_number: Optional[str] = None
    season_number: Optional[str] = None
    size_bytes: Optional[int] = None
    summary: str = ''
    duration_seconds: Optional[float] = None
    id3_complete: Optional[bool] = None
    in_feed: bool = True
    checksum: Optional[str] = None
    checksum_verified_at: Optional[str] = None
    checksum_status: Optional[str] = None  # 'ok' | 'corrupt' | None (never checked)
    files: List[FileOnDisk] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class RenameResult:
    """Outcome of setting (or clearing) an episode's custom_title."""
    podcast_name: str
    episode_id: str
    success: bool
    old_title: Optional[str] = None
    new_title: Optional[str] = None
    renamed_on_disk: bool = False
    old_folder: Optional[str] = None
    new_folder: Optional[str] = None
    detail: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AppSettings:
    """Currently active naming templates."""
    folder_template: str
    filename_template: str


@dataclass
class NamingPreview:
    """Rendered example output for a pair of (possibly not-yet-saved) templates."""
    folder_path: Optional[str] = None
    filename: Optional[str] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class SettingsResult:
    """Outcome of saving new naming templates."""
    success: bool
    folder_template: Optional[str] = None
    filename_template: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ScheduleInfo:
    """A podcast's auto-refresh configuration."""
    interval_hours: Optional[int] = None  # None = disabled
    auto_download: bool = False
    last_checked_at: Optional[str] = None
    podcast_name: Optional[str] = None
    total_episodes: Optional[int] = None
    downloaded_episodes: Optional[int] = None
    podcast_image_url: Optional[str] = None
    display_name: Optional[str] = None  # the feed's own title (author_raw), falling back to podcast_name


@dataclass
class ScheduleResult:
    """Outcome of setting a podcast's auto-refresh schedule."""
    podcast_name: str
    success: bool
    error: Optional[str] = None


@dataclass
class CheckOutcome:
    """Outcome of re-syncing one due podcast's feed."""
    podcast_name: str
    success: bool
    episodes_found: int = 0
    auto_download_triggered: bool = False
    error: Optional[str] = None


@dataclass
class CheckRunResult:
    """Outcome of one scheduler pass over every due podcast."""
    checked: List[CheckOutcome] = field(default_factory=list)


@dataclass
class DiskUsage:
    """Disk usage breakdown for the Storage page (Fase 16b): bucketed by file
    type from a single `os.walk` over the downloads folder, plus
    `shutil.disk_usage()` for the volume's free space."""
    audio_bytes: int = 0
    art_metadata_bytes: int = 0
    orphaned_bytes: int = 0
    total_bytes: int = 0  # volume total, from shutil.disk_usage()
    used_bytes: int = 0   # volume used, from shutil.disk_usage()
    free_bytes: int = 0   # volume free, from shutil.disk_usage()
    error: Optional[str] = None

    @property
    def library_bytes(self) -> int:
        return self.audio_bytes + self.art_metadata_bytes + self.orphaned_bytes


@dataclass
class PlaybackInfo:
    """Resolved audio/cover file paths and metadata for the web player."""
    episode_id: Optional[str] = None
    podcast_name: Optional[str] = None
    title: Optional[str] = None
    summary: str = ''
    audio_path: Optional[str] = None
    cover_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None
