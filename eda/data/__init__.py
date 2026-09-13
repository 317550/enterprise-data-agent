"""Schema, datasets and the database build script."""

from eda.data.fixture_dataset import FIXTURE_NAME, load_fixture_dataset
from eda.data.generator import DEMO_NAME, DemoDataSpec, generate_demo_dataset
from eda.data.schema import SCHEMA_SQL, EXPECTED_TABLES, EXPECTED_VIEWS

__all__ = [
    "FIXTURE_NAME",
    "load_fixture_dataset",
    "DEMO_NAME",
    "DemoDataSpec",
    "generate_demo_dataset",
    "SCHEMA_SQL",
    "EXPECTED_TABLES",
    "EXPECTED_VIEWS",
]
