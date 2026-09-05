from importlib import import_module


def test_package_imports() -> None:
    package = import_module("waterlooworks_flash_next")
    assert package.__doc__
