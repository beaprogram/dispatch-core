"""Scaffold test to verify project structure."""


def test_import_package() -> None:
    """Test that the package can be imported."""
    import dispatch_core

    assert dispatch_core.__version__ == "0.1.0"
