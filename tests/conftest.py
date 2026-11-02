import pytest
from fastapi.testclient import TestClient

from lifeos import auth
from lifeos.app import bootstrap, create_app
from lifeos.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(home=tmp_path / "home")


@pytest.fixture
def tokens(settings):
    conn = bootstrap(settings)
    out = {scope: auth.create(conn, f"test-{scope}", scope) for scope in auth.SCOPES}
    conn.close()
    return out


@pytest.fixture
def client(settings, tokens):
    with TestClient(create_app(settings)) as c:
        c.headers["Authorization"] = f"Bearer {tokens['write']}"
        yield c


@pytest.fixture
def conn(settings, client):
    from lifeos.db import connect
    c = connect(settings.db_path)
    yield c
    c.close()
