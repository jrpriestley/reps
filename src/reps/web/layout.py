"""The page frame: head, top bar and tabs."""

import functools
import hashlib
import html
from importlib import resources

from reps import __version__
from reps.web.icons import icon, logo

STATIC = resources.files("reps.web") / "static"
ASSETS = {
    "app.css": "text/css; charset=utf-8",
    "chart.js": "text/javascript; charset=utf-8",
    "heart.js": "text/javascript; charset=utf-8",
    "describe.js": "text/javascript; charset=utf-8",
    "flow.js": "text/javascript; charset=utf-8",
    "music.js": "text/javascript; charset=utf-8",
    "session.js": "text/javascript; charset=utf-8",
}
TABS = (("today", "/", "Today"), ("week", "/week", "Week"),
        ("history", "/history", "History"), ("me", "/me", "Me"))
FONTS = ("ember-400", "ember-700", "ember-display-700")
# The themes, each its name and whether it's light or dark; System follows the device's.
THEMES = {
    "system": ("System", ""),
    "light": ("Light", "light"),
    "dark": ("Dark", "dark"),
    "paper": ("Paper", "light"),
    "ocean": ("Ocean", "light"),
    "midnight": ("Midnight", "dark"),
    "forest": ("Forest", "dark"),
}
# Each one's --surface, for the browser's own bars.
BARS = {
    "light": "#ffffff", "dark": "#141e1c", "paper": "#fbf8f1", "ocean": "#ffffff",
    "midnight": "#121a2e", "forest": "#141d16",
}


@functools.cache
def asset(name: str) -> bytes:
    return (STATIC / name).read_bytes()


@functools.cache
def asset_version() -> str:
    return hashlib.sha256(b"".join(asset(name) for name in ASSETS)).hexdigest()[:10]


def page(title: str, body: str, scripts: tuple[str, ...] = (), css: str = "") -> str:
    version = asset_version()
    # In the head and before the first paint, so the player and heart rate never blink empty.
    loaded = "".join(
        f'<script type="module" blocking="render" src="/{name}?v={version}"></script>'
        for name in scripts
    )
    fonts = "".join(
        f'<link rel="preload" href="/{name}.woff2" as="font" type="font/woff2" crossorigin>'
        for name in FONTS
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="color-scheme" content="light dark">'
        f'<meta name="theme-color" content="{BARS["light"]}" '
        'media="(prefers-color-scheme: light)">'
        f'<meta name="theme-color" content="{BARS["dark"]}" '
        'media="(prefers-color-scheme: dark)">'
        '<meta name="apple-mobile-web-app-title" content="reps">'
        '<link rel="manifest" href="/manifest.webmanifest">'
        '<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-180.png">'
        f'{fonts}<link rel="stylesheet" href="/app.css?v={version}">'
        f"<title>{html.escape(title)} · reps</title>{loaded}</head>"
        f'<body class="{css}">{body}</body></html>'
    )


def framed(
    active: str, title: str, content: str, person: str, scripts: tuple[str, ...] = ()
) -> str:
    """Return a signed-in page: top bar, the screen and the tabs."""
    tabs = "".join(
        f'<a href="{href}"{" aria-current=page" if key == active else ""}>'
        f"{icon(key)}<span>{label}</span></a>"
        for key, href, label in TABS
    )
    body = (
        f'<header class="topbar"><div class="topbar-inner"><a class="brand" href="/">{logo()}'
        f'<span>reps</span></a><nav class="primary" aria-label="Sections">{tabs}</nav>'
        f'<span class="heart" data-heart></span><span class="who">{html.escape(person)}</span>'
        "</div></header>"
        f"<main>{content}</main>"
        '<footer class="player" data-music aria-label="Music"></footer>'
    )
    return page(title, body, ("heart.js", "music.js", *scripts))


def bare(title: str, content: str, css: str = "bare") -> str:
    """Return a page without tabs: sign-in and first setup."""
    return page(title, f"<main>{content}</main>", css=css)


def login_page(error: str = "") -> str:
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return bare(
        "Sign in",
        f'<div class="login"><div class="brand">{logo()}<h1>reps</h1></div>'
        f'<div class="panel">{message}<form method="post" action="/login">'
        '<label>User <input name="user" autocomplete="username" autocapitalize="none" required>'
        '</label><label>Password <input type="password" name="password" '
        'autocomplete="current-password" required></label>'
        '<button class="primary">Sign in</button></form></div>'
        f'<p class="muted note version">reps {__version__}</p></div>',
    )
