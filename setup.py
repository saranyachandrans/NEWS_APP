
from setuptools import setup, find_packages

setup(
    name="live-news-sentiment",
    version="0.1.0",
    description="Live Indian news collection using RSS feeds",
    packages=find_packages(),
    install_requires=[
        "requests",
        "feedparser",
        "PyYAML"
    ],
    python_requires=">=3.10"
)
