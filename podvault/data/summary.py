"""
Summary generation functions for podcast statistics.
"""

import pandas as pd
from podvault.config import EPISODE_STATUS_DOWNLOADED


def summarise_podcasts(df):
    """
    Summarize podcast data including download statistics.
    
    Parameters:
    df: pandas DataFrame containing podcast episodes with columns:
        - 'author': podcast name
        - 'status': 'downloaded' or 'not downloaded'
        - 'published' or 'published_parsed': episode publication date
    
    Returns:
    pandas DataFrame with summary statistics for each podcast containing:
        - name: Podcast name
        - num_episodes: Total number of episodes
        - num_episodes_downloaded: Number of downloaded episodes
        - num_episodes_not_downloaded: Number of not downloaded episodes
        - pct_episodes_downloaded: Percentage of episodes downloaded
        - dataframe_name: Suggested dataframe variable name
        - last_episode_downloaded_date: Date of last downloaded episode
    """
    summaries = []
    
    # Group by podcast name (author column)
    for podcast_name in df['author'].unique():
        podcast_df = df[df['author'] == podcast_name]
        
        # Get the number of episodes
        num_episodes = len(podcast_df)
        
        # Get the number of episodes downloaded
        num_episodes_downloaded = len(podcast_df[podcast_df['status'] == EPISODE_STATUS_DOWNLOADED])
        
        # Get the number of episodes not downloaded
        num_episodes_not_downloaded = num_episodes - num_episodes_downloaded
        
        # Calculate the % of episodes downloaded
        pct_downloaded = (num_episodes_downloaded / num_episodes * 100) if num_episodes > 0 else 0
        
        # Get the date of the last episode downloaded
        downloaded_episodes = podcast_df[podcast_df['status'] == EPISODE_STATUS_DOWNLOADED]
        if len(downloaded_episodes) > 0:
            # Try to get published date - handle both 'published' and 'published_parsed' formats
            if 'published' in downloaded_episodes.columns:
                last_downloaded_date = downloaded_episodes['published'].max()
            elif 'published_parsed' in downloaded_episodes.columns:
                # Convert published_parsed tuples to datetime
                dates = pd.to_datetime([pd.Timestamp(*d) if d else None for d in downloaded_episodes['published_parsed']])
                last_downloaded_date = dates.max()
            else:
                last_downloaded_date = None
        else:
            last_downloaded_date = None
        
        # Get the dataframe name (variable name is not directly accessible, so we'll use the podcast name)
        dataframe_name = f"df_{podcast_name}"
        
        summary = {
            'name': podcast_name,
            'num_episodes': num_episodes,
            'num_episodes_downloaded': num_episodes_downloaded,
            'num_episodes_not_downloaded': num_episodes_not_downloaded,
            'pct_episodes_downloaded': round(pct_downloaded, 2),
            'dataframe_name': dataframe_name,
            'last_episode_downloaded_date': last_downloaded_date
        }
        
        summaries.append(summary)
    
    # Return as a DataFrame
    return pd.DataFrame(summaries)




