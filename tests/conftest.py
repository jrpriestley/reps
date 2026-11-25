import http.client
import json
import threading
import urllib.parse

import pytest

from reps.config import Config
from reps.web import auth
from reps.web.server import Server


class Client:
    """A browser's worth of HTTP: keeps cookies, says which site it is on."""

    def __init__(self, port):
        self.port, self.cookies = port, {}
        self.host = f"reps.test:{port}"

    def send(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        sent = {"Host": self.host, "Origin": f"http://{self.host}"}
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        connection.request(method, path, body, sent | (headers or {}))
        response = connection.getresponse()
        text = response.read().decode(errors="replace")
        for cookie in response.headers.get_all("Set-Cookie") or []:
            key, _, value = cookie.split(";")[0].partition("=")
            self.cookies[key] = value
        connection.close()
        return response, text

    def get(self, path, **headers):
        return self.send("GET", path, headers=headers)

    def post(self, path, fields=(), **headers):
        body = urllib.parse.urlencode(fields, doseq=True)
        kind = {"Content-Type": "application/x-www-form-urlencoded"}
        return self.send("POST", path, body, kind | headers)

    def api(self, path, data):
        response, text = self.send("POST", path, json.dumps(data))
        return response.status, json.loads(text)

    def sign_in(self, user="al", password="right"):
        return self.post("/login", {"user": user, "password": password})

    def set_up(self, user="al", equipment=("dumbbells", "tower", "bike")):
        self.sign_in(user)
        self.post("/setup/goals", {"goals": ["strength", "fitness"]})
        self.post("/setup/equipment", {"equipment": equipment})
        self.post("/setup/time", {"minutes": "45"})
        self.post("/setup/days", {"days": ["0", "2", "4"]})
        self.post("/setup/body", {"experience": "returning", "body_kg": ""})
        return self


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auth.Shadow, "verify",
        lambda self, user, password: user in self.users and password == "right",
    )
    config = Config(
        host="reps.test", port=0, bind="127.0.0.1", state_dir=tmp_path, tls_dir=None,
        copies_sent=tmp_path / "sent",
    )
    running = Server(config, None)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield running
    running.shutdown()
    running.server_close()


@pytest.fixture
def client(server):
    return Client(server.server_address[1])


@pytest.fixture
def other(server):
    return Client(server.server_address[1])


@pytest.fixture
def store(server):
    return server.RequestHandlerClass.app.store
