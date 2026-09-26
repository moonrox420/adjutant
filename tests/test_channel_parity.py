import pytest
from check_channel_parity import main, violations


@pytest.mark.parametrize(
    "source",
    [
        'if channel == "meta":\n    run()',
        'if "google_ads" != channel:\n    run()',
        'if channel in {"tiktok", "youtube"}:\n    run()',
        'META = "meta"\nif channel == META:\n    run()',
        'match channel:\n    case "reddit":\n        run()',
    ],
)
def test_channel_comparisons_and_cases_are_rejected(source):
    assert violations(source, "service.py")


def test_registry_lookups_and_data_literals_are_allowed():
    assert (
        violations(
            'channels = ["meta", "google_ads"]\nvalue = registry[channel]', "service.py"
        )
        == []
    )
    assert violations('if record["channel"] == channel:\n    run()', "service.py") == []


def test_repository_preserves_adapter_boundary():
    assert main() == 0
