"""The admin offers a French / English language switcher."""

import pytest
from django.test import Client


@pytest.mark.django_db
def test_login_page_has_language_switcher():
    response = Client().get("/login/")
    content = response.content.decode()
    assert 'action="/i18n/setlang/"' in content
    assert 'value="fr" selected' in content
    assert 'value="en"' in content


@pytest.mark.django_db
def test_switching_to_english_then_back_to_french():
    client = Client()
    client.post("/i18n/setlang/", {"language": "en", "next": "/login/"})
    assert 'lang="en"' in client.get("/login/").content.decode()
    client.post("/i18n/setlang/", {"language": "fr", "next": "/login/"})
    assert 'lang="fr"' in client.get("/login/").content.decode()


@pytest.mark.django_db
def test_browser_language_is_honoured_without_cookie():
    response = Client(headers={"accept-language": "en"}).get("/login/")
    assert 'lang="en"' in response.content.decode()
