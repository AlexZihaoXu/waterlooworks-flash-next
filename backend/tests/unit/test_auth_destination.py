"""Credential-free destination checks; no browser, network, or authentication."""

from waterlooworks_flash_next.duo import authenticated_destination


def test_waterlooworks_with_observed_navigation_is_authenticated() -> None:
    assert authenticated_destination("https://waterlooworks.uwaterloo.ca/", True, True)


def test_waterlooworks_origin_alone_is_not_authentication() -> None:
    assert not authenticated_destination(
        "https://waterlooworks.uwaterloo.ca/", False, False
    )
    assert not authenticated_destination(
        "https://waterlooworks.uwaterloo.ca/", True, False
    )
    assert not authenticated_destination(
        "https://waterlooworks.uwaterloo.ca/", False, True
    )


def test_authentication_hosts_are_not_the_destination() -> None:
    assert not authenticated_destination("https://adfs.uwaterloo.ca/", True, True)
    assert not authenticated_destination(
        "https://api-4ccc589b.duosecurity.com/", True, True
    )
