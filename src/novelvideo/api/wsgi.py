"""ASGI entrypoint for the standalone NovelVideo API."""

from novelvideo.env import load_project_dotenv

load_project_dotenv(override=False)

from novelvideo.api.app import app  # noqa: F401
