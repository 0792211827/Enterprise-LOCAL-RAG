from src.config import get_settings
from src.services.opensearch.factory import make_opensearch_client


def test_opensearch_client_health():
    client = make_opensearch_client()

    health = client.health_check()
    assert isinstance(health, bool)


def test_settings_loading():
    settings = get_settings()

    assert hasattr(settings, "app_version")
    assert hasattr(settings, "service_name")
    assert hasattr(settings, "environment")
