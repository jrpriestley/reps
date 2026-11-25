"""Settings: the defaults are homehub's; /etc/reps/reps.toml (or $REPS_CONFIG) overrides."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    host: str = "reps"
    port: int = 8090
    bind: str = "0.0.0.0"  # the router firewall drops everything from the internet
    state_dir: Path = Path("/var/lib/reps")
    tls_dir: Path | None = Path("/etc/reps/tls")
    shadow_file: Path = Path("/etc/shadow")
    ember_dir: Path = Path("/usr/local/share/ember")  # Amazon Ember, kept out of the repo
    spotify_dir: Path = Path("/usr/local/share/spotify")  # their logo, kept out of the repo
    copies_sent: Path = Path("/var/lib/reps-outbox/sent")  # when copies last left homehub
    describe_socket: Path = Path("/run/reps-describe/sock")
    spotify_client: str = ""  # the developer app's client ID; not a secret
    users: dict[str, str] = field(default_factory=lambda: {"al": "al", "bo": "bo-web"})

    @property
    def database(self) -> Path:
        return self.state_dir / "reps.db"


def copied_at(config: Config) -> float | None:
    """Return when the copies last reached Proton Drive, or None if they never have."""
    try:
        return float(config.copies_sent.read_text())
    except (OSError, ValueError):
        return None


def heart_key() -> str:
    """Return the heart rate receiver's key, or '' when there is none (the feed is off)."""
    folder = os.environ.get("CREDENTIALS_DIRECTORY")
    file = Path(folder) / "heart.key" if folder else None
    return file.read_text().strip() if file and file.exists() else ""


def load_config() -> Config:
    path = Path(os.environ.get("REPS_CONFIG", "/etc/reps/reps.toml"))
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    web = data.get("web", {})
    paths = data.get("paths", {})
    settings = {key: web[key] for key in ("host", "port", "bind", "users") if key in web}
    for key in ("state_dir", "tls_dir", "shadow_file", "ember_dir", "spotify_dir"):
        if key in paths:
            settings[key] = Path(paths[key]) if paths[key] else None
    if client := data.get("spotify", {}).get("client_id", ""):
        settings["spotify_client"] = client
    return Config(**settings)
