"""
Setup script for PodVault package.
"""

from setuptools import setup, find_packages

setup(
    name='podvault',
    version='1.1.0',
    description='A podcast archiving tool: download, organize, and preserve podcast episodes from RSS feeds',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'podvault.web': ['templates/*.html', 'static/fonts/*.woff2'],
    },
    install_requires=[
        'pandas',
        'numpy',
        'feedparser',
        'requests',
        'mutagen',  # For ID3 tag management
        'tqdm',  # For progress bars
        'fastapi',  # Web interface
        'uvicorn',  # ASGI server for the web interface
        'jinja2',  # Templates for the web interface
        'python-multipart',  # Form parsing for the web interface
    ],
    extras_require={
        'test': [
            'pytest',
            'httpx',  # required by fastapi.testclient.TestClient
        ],
    },
    python_requires='>=3.7',
    entry_points={
        'console_scripts': [
            'podvault=podvault.cli:main',
        ],
    },
)




