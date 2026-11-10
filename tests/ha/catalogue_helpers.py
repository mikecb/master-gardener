"""Serve the plant catalogue download from a small fixture file."""

from pathlib import Path

from custom_components.master_gardener.plant_store import SOURCE_URL

FIXTURE_CSV = Path(__file__).parents[1] / "fixtures" / "plants.csv"


def mock_catalogue(aioclient_mock, content: bytes | None = None, etag: str = '"v1"'):
    """Serve the plant catalogue download from the fixture CSV."""
    aioclient_mock.get(
        SOURCE_URL,
        content=FIXTURE_CSV.read_bytes() if content is None else content,
        headers={"ETag": etag},
    )
