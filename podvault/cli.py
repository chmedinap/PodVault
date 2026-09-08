"""
Command Line Interface for PodVault.

Thin argparse wrapper: all business logic lives in `podvault.services`
so the same logic can be reused by a web interface later without
duplicating it here.
"""

import argparse
import sys

from podvault import services
from podvault.download.utils import TEMPLATE_VARIABLES
from podvault.validation import validate_file_path


def process_feeds_file(feeds_file='feeds.txt'):
    """Process all feeds from a feeds file and print a human-readable report."""
    result = services.process_feeds_file(feeds_file)

    if result.error:
        print(f"❌ {result.error}")
        return False

    print(f"📋 Found {len(result.feeds)} feed(s) to process\n")

    for idx, feed in enumerate(result.feeds, 1):
        print(f"{'='*60}")
        print(f"Processing feed {idx}/{len(result.feeds)}: {feed.feed_url}")
        print(f"{'='*60}")

        if feed.skipped:
            print(f"⚠️  Invalid feed URL: {feed.error}")
            print(f"   Skipping feed: {feed.feed_url}\n")
            continue

        if not feed.success:
            print(f"❌ Error processing feed '{feed.feed_url}': {feed.error}")
            print(f"   Skipping to next feed...\n")
            continue

        print(f"📻 Podcast: {feed.podcast_name}")
        print(f"📊 Episodes found: {feed.episodes_found}")
        print(f"✅ Successfully processed: {feed.podcast_name}\n")

    print(f"{'='*60}")
    print("✅ Feed processing complete!")
    print(f"{'='*60}")
    return result.success


def list_podcasts():
    """List all podcasts in the database."""
    result = services.list_podcasts()

    if result.error:
        print(f"❌ {result.error}")
        return False

    if not result.podcasts:
        print("📭 No podcasts found in database.")
        print("   Process feeds first using: podvault process-feeds")
        return False

    print(f"📻 Found {len(result.podcasts)} podcast(s):\n")
    for idx, podcast in enumerate(result.podcasts, 1):
        print(f"{idx}. {podcast.name}")
        if podcast.total_episodes is not None:
            print(f"   Episodes: {podcast.downloaded_episodes}/{podcast.total_episodes} downloaded")
        print()

    return True


def show_status(podcast_name=None):
    """Show download status for podcast(s)."""
    result = services.get_status(podcast_name)

    if result.error:
        print(f"❌ {result.error}")
        return False

    if podcast_name:
        print(f"\n📊 Summary for '{podcast_name}':")
        print(f"   Episodes to download: {result.pending_by_podcast.get(podcast_name, 0)}")
    else:
        print("\n📊 Download Status:")
        for name, count in result.pending_by_podcast.items():
            print(f"   {name}: {count} episode(s) to download")

    return True


def download_episodes(podcast_name, last_only=False):
    """Download episodes for a podcast."""
    try:
        if last_only:
            print(f"📥 Downloading last episode for '{podcast_name}'...")
            result = services.download_last(podcast_name)
        else:
            print(f"📥 Downloading all episodes for '{podcast_name}'...")
            result = services.download_all(podcast_name)

        if result.error:
            print(f"❌ {result.error}")
            return False

        if result.success:
            print("✅ Download complete!")
        else:
            print("⚠️  No episodes were downloaded.")

        return result.success

    except KeyboardInterrupt:
        print("\n⚠️  Download interrupted by user.")
        return False


def add_feed(feed_url):
    """Add a single feed to feeds.txt and process it."""
    result = services.add_feed(feed_url)

    if result.error:
        print(f"❌ {result.error}")
        return False

    for feed in result.feeds:
        if feed.success:
            print(f"✅ Successfully processed: {feed.podcast_name} ({feed.episodes_found} episodes)")
        else:
            print(f"❌ Error processing feed '{feed.feed_url}': {feed.error}")

    return result.success


def migrate_layout(apply=False):
    """Migrate downloaded episodes to the year/month/label folder layout."""
    result = services.migrate_layout(apply=apply)

    if result.error:
        print(f"❌ {result.error}")
        return False

    if not result.moves:
        print("📭 No downloaded episodes found to migrate.")
        return True

    mode = "Applying changes" if apply else "Dry run (nothing moved yet)"
    print(f"{'='*60}")
    print(f"Folder migration — {mode}")
    print(f"{'='*60}\n")

    counts = {'planned': 0, 'moved': 0, 'skipped': 0, 'error': 0}
    for move in result.moves:
        counts[move.status] = counts.get(move.status, 0) + 1
        icon = {'planned': '➡️ ', 'moved': '✅', 'skipped': '⏭️ ', 'error': '❌'}[move.status]
        print(f"{icon} [{move.podcast_name}] {move.episode_title}")
        print(f"   {move.old_folder}")
        print(f"   -> {move.new_folder}")
        if move.detail:
            print(f"   ({move.detail})")
        print()

    print(f"{'='*60}")
    print(f"Total: {len(result.moves)} episode(s) — "
          f"{counts['planned']} planned, {counts['moved']} moved, "
          f"{counts['skipped']} skipped, {counts['error']} error(s)")
    if not apply and counts['planned'] > 0:
        print("\nThis was a dry run. Run 'podvault migrate-layout --apply' to actually move the files.")
    print(f"{'='*60}")

    return result.success


def verify_files(podcast_name=None):
    """Check that downloaded episodes still exist on disk."""
    result = services.verify_files(podcast_name)

    if result.error:
        print(f"❌ {result.error}")
        return False

    for podcast in result.podcasts:
        if podcast.missing_episodes:
            print(f"\n⚠️  '{podcast.podcast_name}': {len(podcast.missing_episodes)} file(s) missing on disk:")
            for episode in podcast.missing_episodes:
                print(f"   - {episode.title} ({episode.saved_path or 'no saved path'})")
        else:
            print(f"✓ '{podcast.podcast_name}': every downloaded file is present")

    if result.total_missing:
        print(f"\n📊 Total: {result.total_missing} episode(s) marked as 'missing'.")
        print("   Run 'podvault download <podcast>' to download them again.")
    else:
        print("\n✅ Everything checks out, no files are missing.")

    return True


def list_episodes(podcast_name):
    """List episodes of a podcast with their id, title, and status."""
    result = services.list_episodes(podcast_name)

    if result.error:
        print(f"❌ {result.error}")
        return False

    if not result.episodes:
        print(f"📭 No episodes found for '{podcast_name}'.")
        return True

    print(f"📻 {len(result.episodes)} episode(s) for '{podcast_name}':\n")
    for ep in result.episodes:
        display_title = ep.custom_title or ep.title
        marker = " (renamed)" if ep.custom_title else ""
        print(f"[{ep.status}] {ep.episode_id}")
        print(f"   {display_title}{marker}")
        if ep.saved_path:
            print(f"   {ep.saved_path}")
        print()

    return True


def rename_episode(podcast_name, episode_id, custom_title):
    """Set (or clear) an episode's custom display name."""
    result = services.rename_episode(podcast_name, episode_id, custom_title)

    if result.error:
        print(f"❌ {result.error}")
        return False

    print(f"✅ '{result.old_title}' -> '{result.new_title}'")
    if result.renamed_on_disk:
        print(f"   {result.old_folder}")
        print(f"   -> {result.new_folder}")
    elif result.detail:
        print(f"   ({result.detail})")

    return True


def show_config(folder_template=None, filename_template=None):
    """Show, or update, the folder/filename naming templates."""
    if folder_template is None and filename_template is None:
        settings = services.get_settings()
        print("📐 Current naming templates:")
        print(f"   Folder:   {settings.folder_template}")
        print(f"   Filename: {settings.filename_template}")
        print(f"\n   Available variables: {', '.join('{' + v + '}' for v in TEMPLATE_VARIABLES)}")
        return True

    current = services.get_settings()
    new_folder_template = folder_template if folder_template is not None else current.folder_template
    new_filename_template = filename_template if filename_template is not None else current.filename_template

    result = services.update_settings(new_folder_template, new_filename_template)
    if result.error:
        print(f"❌ {result.error}")
        return False

    print("✅ Templates updated:")
    print(f"   Folder:   {result.folder_template}")
    print(f"   Filename: {result.filename_template}")
    print("\n   (Doesn't affect already-downloaded episodes; run 'podvault migrate-layout' if you want to reorganize them.)")

    return True


def show_schedule(podcast_name, interval_hours=None, disable=False, auto_download=None):
    """Show, or update, a podcast's auto-refresh schedule."""
    if interval_hours is None and not disable and auto_download is None:
        schedule = services.get_podcast_schedule(podcast_name)
        if schedule.interval_hours is None:
            print(f"📆 '{podcast_name}': auto-refresh disabled")
        else:
            print(f"📆 '{podcast_name}': checks every {schedule.interval_hours}h")
            print(f"   Auto-download: {'yes' if schedule.auto_download else 'no'}")
            print(f"   Last checked: {schedule.last_checked_at or 'never'}")
        return True

    new_interval = None if disable else interval_hours
    if new_interval is None and not disable:
        # Only --auto-download/--no-auto-download was passed: keep the existing interval.
        new_interval = services.get_podcast_schedule(podcast_name).interval_hours

    new_auto_download = auto_download if auto_download is not None else services.get_podcast_schedule(podcast_name).auto_download

    result = services.set_podcast_schedule(podcast_name, new_interval, new_auto_download)
    if result.error:
        print(f"❌ {result.error}")
        return False

    if new_interval is None:
        print(f"✅ '{podcast_name}': auto-refresh disabled")
    else:
        print(f"✅ '{podcast_name}': checks every {new_interval}h, auto-download: {'yes' if new_auto_download else 'no'}")

    return True


def serve(host='127.0.0.1', port=8000, reload=False):
    """Start the web interface."""
    import uvicorn

    if reload:
        print("⚠️  --reload starts uvicorn's reloader in a subprocess; the auto-refresh scheduler won't run there, skipping it.")
    else:
        from podvault.scheduler import start_scheduler
        start_scheduler()

    print(f"🌐 Starting PodVault web interface at http://{host}:{port}")
    uvicorn.run('podvault.web.app:app', host=host, port=port, reload=reload)
    return True


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='PodVault - Download and manage podcast episodes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  podvault process-feeds              # Process all feeds from feeds.txt
  podvault list                       # List all podcasts
  podvault status                     # Show download status
  podvault download podcast_name      # Download all episodes
  podvault download-last podcast_name # Download last episode only
  podvault add <feed-url>             # Add and process a feed
  podvault migrate-layout             # Preview folder layout migration (dry run)
  podvault migrate-layout --apply     # Actually move files to the new layout
  podvault verify                     # Check downloaded files still exist on disk
  podvault episodes podcast_name      # List episodes with their id and status
  podvault rename podcast_name <id> "New Title"  # Set a custom display name
  podvault rename podcast_name <id> ""           # Clear the custom name
  podvault serve                      # Start the web interface
  podvault config                     # Show the current naming templates
  podvault config --folder-template "{podcast_name}/{year}/{episode_label}"
  podvault schedule podcast_name                      # Show its auto-refresh schedule
  podvault schedule podcast_name --interval-hours 12 --auto-download
  podvault schedule podcast_name --disable            # Turn off auto-refresh
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Process feeds command
    process_parser = subparsers.add_parser('process-feeds', help='Process all feeds from feeds.txt')
    process_parser.add_argument('--file', default='feeds.txt', help='Feeds file path (default: feeds.txt)')

    # List podcasts command
    subparsers.add_parser('list', help='List all podcasts in database')

    # Status command
    status_parser = subparsers.add_parser('status', help='Show download status')
    status_parser.add_argument('podcast', nargs='?', help='Podcast name (optional, shows all if omitted)')

    # Download command
    download_parser = subparsers.add_parser('download', help='Download all episodes for a podcast')
    download_parser.add_argument('podcast', help='Podcast name')

    # Download last command
    download_last_parser = subparsers.add_parser('download-last', help='Download only the last episode')
    download_last_parser.add_argument('podcast', help='Podcast name')

    # Add feed command
    add_parser = subparsers.add_parser('add', help='Add a feed URL and process it')
    add_parser.add_argument('feed_url', help='RSS feed URL or Apple Podcast link')

    # Migrate layout command
    migrate_parser = subparsers.add_parser(
        'migrate-layout',
        help='Migrate downloaded episodes to the year/month/label folder layout'
    )
    migrate_parser.add_argument(
        '--apply', action='store_true',
        help='Actually move files and update the database (default: dry run, only reports what would move)'
    )

    # Verify command
    verify_parser = subparsers.add_parser('verify', help='Check that downloaded episodes still exist on disk')
    verify_parser.add_argument('podcast', nargs='?', help='Podcast name (optional, checks all if omitted)')

    # Episodes command
    episodes_parser = subparsers.add_parser('episodes', help='List episodes of a podcast with their id and status')
    episodes_parser.add_argument('podcast', help='Podcast name')

    # Rename command
    rename_parser = subparsers.add_parser('rename', help="Set (or clear) an episode's custom display name")
    rename_parser.add_argument('podcast', help='Podcast name')
    rename_parser.add_argument('episode_id', help="Episode id (see 'podvault episodes')")
    rename_parser.add_argument('title', help='New display name, or "" to clear the override')

    # Serve command
    serve_parser = subparsers.add_parser('serve', help='Start the web interface')
    serve_parser.add_argument('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    serve_parser.add_argument('--port', type=int, default=8000, help='Port to bind to (default: 8000)')
    serve_parser.add_argument('--reload', action='store_true', help='Auto-reload on code changes (development only)')

    # Config command
    config_parser = subparsers.add_parser('config', help='Show or update the folder/filename naming templates')
    config_parser.add_argument('--folder-template', help='New folder template (see --help variables list)')
    config_parser.add_argument('--filename-template', help='New filename template')

    # Schedule command
    schedule_parser = subparsers.add_parser('schedule', help="Show or update a podcast's auto-refresh schedule")
    schedule_parser.add_argument('podcast', help='Podcast name')
    schedule_parser.add_argument('--interval-hours', type=int, help='Hours between automatic feed checks (e.g. 6, 12, 24, 168)')
    schedule_parser.add_argument('--disable', action='store_true', help='Disable automatic feed checks for this podcast')
    auto_download_group = schedule_parser.add_mutually_exclusive_group()
    auto_download_group.add_argument(
        '--auto-download', dest='auto_download', action='store_true', default=None,
        help='Automatically download new episodes found during a scheduled check',
    )
    auto_download_group.add_argument(
        '--no-auto-download', dest='auto_download', action='store_false', default=None,
        help='Only refresh the episode list on a scheduled check; download manually',
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == 'process-feeds':
            # Validate file path argument
            is_valid, error = validate_file_path(args.file)
            if not is_valid:
                print(f"❌ Invalid file path '{args.file}': {error}")
                return 1
            success = process_feeds_file(args.file)
        elif args.command == 'list':
            success = list_podcasts()
        elif args.command == 'status':
            success = show_status(args.podcast)
        elif args.command == 'download':
            success = download_episodes(args.podcast, last_only=False)
        elif args.command == 'download-last':
            success = download_episodes(args.podcast, last_only=True)
        elif args.command == 'add':
            success = add_feed(args.feed_url)
        elif args.command == 'migrate-layout':
            success = migrate_layout(apply=args.apply)
        elif args.command == 'verify':
            success = verify_files(args.podcast)
        elif args.command == 'episodes':
            success = list_episodes(args.podcast)
        elif args.command == 'rename':
            success = rename_episode(args.podcast, args.episode_id, args.title)
        elif args.command == 'serve':
            success = serve(host=args.host, port=args.port, reload=args.reload)
        elif args.command == 'config':
            success = show_config(folder_template=args.folder_template, filename_template=args.filename_template)
        elif args.command == 'schedule':
            success = show_schedule(
                args.podcast, interval_hours=args.interval_hours,
                disable=args.disable, auto_download=args.auto_download,
            )
        else:
            parser.print_help()
            return 1

        return 0 if success else 1

    except KeyboardInterrupt:
        print("\n⚠️  Operation cancelled by user.")
        return 130
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
