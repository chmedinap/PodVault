"""
Centralized logging configuration for PodVault.

This module provides a single point of configuration for all logging
across the application, eliminating code duplication and ensuring
consistent logging behavior. Uses logging.config.dictConfig() for
flexible and maintainable configuration.
"""

import logging
import logging.config
import sys
from pathlib import Path
from typing import Optional
from podvault import config

# Track if logging has been configured to avoid reconfiguration
_logging_configured = False


def _get_logging_config() -> dict:
    """
    Build logging configuration dictionary.
    
    Returns:
    dict: Logging configuration for dictConfig()
    """
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    
    # Base configuration
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S'
            },
            'detailed': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(pathname)s:%(lineno)d - %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S'
            }
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'level': log_level,
                'formatter': 'standard',
                'stream': sys.stdout
            }
        },
        'loggers': {
            'podvault': {
                'level': log_level,
                'handlers': ['console'],
                'propagate': False
            }
        },
        'root': {
            'level': log_level,
            'handlers': ['console']
        }
    }
    
    # Add file handler if configured
    if config.LOG_FILE:
        try:
            # Ensure log file directory exists
            log_path = Path(config.LOG_FILE)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            logging_config['handlers']['file'] = {
                'class': 'logging.FileHandler',
                'level': log_level,
                'formatter': 'detailed',
                'filename': config.LOG_FILE,
                'mode': 'a',
                'encoding': 'utf-8'
            }
            
            # Add file handler to loggers
            logging_config['loggers']['podvault']['handlers'].append('file')
            logging_config['root']['handlers'].append('file')
        except (OSError, PermissionError) as e:
            # If file logging fails, log to console only
            print(f"Warning: Could not set up file logging to {config.LOG_FILE}: {e}", file=sys.stderr)
    
    return logging_config


def configure_logging() -> None:
    """
    Configure logging for the entire application.
    
    This function should be called once at application startup to configure
    all logging. It uses dictConfig() for flexible configuration management.
    Subsequent calls are safe and will not reconfigure logging.
    
    Example:
        >>> from podvault.logging_config import configure_logging
        >>> configure_logging()
        >>> logger = logging.getLogger(__name__)
        >>> logger.info("This will be logged")
    """
    global _logging_configured
    
    if _logging_configured:
        return
    
    logging_config = _get_logging_config()
    logging.config.dictConfig(logging_config)
    _logging_configured = True


def setup_logging(logger_name: Optional[str] = None) -> logging.Logger:
    """
    Set up logging for a module with centralized configuration.
    
    This function ensures that logging is configured consistently across
    all modules. It automatically configures the root logger if not already
    configured, then returns a logger for the specified module.
    
    Parameters:
    logger_name: Name of the logger (typically __name__). If None, uses root logger.
    
    Returns:
    logging.Logger: Configured logger instance
    
    Example:
        >>> from podvault.logging_config import setup_logging
        >>> logger = setup_logging(__name__)
        >>> logger.info("This will be logged")
    """
    # Ensure logging is configured
    configure_logging()
    
    # Get logger for the specified name
    if logger_name:
        # Ensure logger is under podvault namespace
        if not logger_name.startswith('podvault'):
            logger_name = f'podvault.{logger_name}'
        logger = logging.getLogger(logger_name)
    else:
        logger = logging.getLogger()
    
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger instance with centralized configuration.
    
    This is a convenience function that calls setup_logging().
    Use this when you just need a logger without worrying about setup.
    
    Parameters:
    name: Name of the logger (typically __name__). If None, uses root logger.
    
    Returns:
    logging.Logger: Configured logger instance
    
    Example:
        >>> from podvault.logging_config import get_logger
        >>> logger = get_logger(__name__)
    """
    return setup_logging(name)


# Configure logging when module is imported
configure_logging()

