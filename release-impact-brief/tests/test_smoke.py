import release_impact_brief


def test_package_imports_with_version() -> None:
    assert release_impact_brief.__version__ == "0.0.1"
