from django.test import Client


def test_health_endpoint():
    response = Client().get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_page_renders():
    response = Client().get("/")
    assert response.status_code == 200
    assert b"RefundOps" in response.content
