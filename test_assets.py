"""Checks on the crest/photo endpoint.

It takes an id, never a URL, so the thing to pin is that it stays that
way -- anything other than a known kind plus digits must be refused.
"""
import app


def _handler():
    class Fake(app.Handler):
        def __init__(self):
            self.path = ""
            self.sent = []
        def send_error(self, code, *a, **k):
            self.sent.append(("error", code))
        def send_response(self, code):
            self.sent.append(("ok", code))
        def send_header(self, *a):
            pass
        def end_headers(self):
            pass
    return Fake()


def test_only_known_asset_kinds_are_served():
    for bad in ("/asset/badge/../../etc/passwd", "/asset/evil/3", "/asset/badge/abc",
                "/asset/badge/3/extra", "/asset/", "/asset/player/x1"):
        h = _handler()
        h._asset(bad)
        assert h.sent and h.sent[0] == ("error", 404), (bad, h.sent)


def test_asset_kinds_point_only_at_the_premier_league_cdn():
    for kind, (url, content_type, ext) in app.ASSETS.items():
        assert url.startswith("https://resources.premierleague.com/"), kind
        assert url.count("%s") == 1, kind
        assert ext in ("svg", "png") and content_type.startswith("image/"), kind


def test_a_code_cannot_escape_the_cache_directory():
    import os
    for code in ("3", "223094"):
        for kind, (_, _, ext) in app.ASSETS.items():
            local = os.path.join(app.ASSET_DIR, "%s-%s.%s" % (kind, code, ext))
            assert os.path.abspath(local).startswith(os.path.abspath(app.ASSET_DIR))
