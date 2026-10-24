"""Local web app for the league table.

  python3 app.py            # then open http://127.0.0.1:8000

The FPL API sends no CORS headers, so a page cannot fetch it directly --
this server does the fetching and hands the browser finished JSON. It is
a local dashboard: bound to localhost, stdlib only, no auth.
"""
import datetime
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fpl
import insights
import robot
import standings
import transfers

HERE = os.path.dirname(os.path.abspath(__file__))
ROBOT_CACHE = {}
# teams.json is read, changed and written back by the transfer endpoints;
# the server is threaded, so two submissions must not interleave.
LEAGUE_LOCK = threading.Lock()
ASSET_DIR = os.path.join(fpl.CACHE_DIR, "assets")

# Club crests and player photos, fetched once and then served from disk so the
# page works offline and nothing hotlinks on every view. Only these two shapes
# are allowed, and the code must be digits -- this endpoint takes an id, never
# a URL, so it cannot be pointed at anything else.
ASSETS = {
    "badge": ("https://resources.premierleague.com/premierleague/badges/t%s.svg",
              "image/svg+xml", "svg"),
    "player": ("https://resources.premierleague.com/premierleague/photos/players/110x140/p%s.png",
               "image/png", "png"),
}
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, exc):
        """Surface the error in the page, not just the console."""
        body = json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)}).encode("utf-8")
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _table(self, ttl):
        try:
            return self._send(json.dumps(standings.build(ttl)), "application/json")
        except Exception as exc:
            return self._fail(exc)

    def _robot(self):
        """Best possible squad, over whichever gameweeks were asked for.

        The full-season search takes a few seconds, so results are kept for
        the life of the process -- the squads cannot change until a refresh
        pulls new goals in.
        """
        query = urllib.parse.urlparse(self.path).query
        weeks = urllib.parse.parse_qs(query).get("weeks", [""])[0]
        weeks = [int(w) for w in weeks.split(",") if w.strip().isdigit()] or None
        key = tuple(weeks or ())
        if key in ROBOT_CACHE:
            return self._send(ROBOT_CACHE[key], "application/json")
        try:
            out = robot.build(weeks)
            players = {p["id"]: p for p in fpl.players()}
            names = {i: p["name"] for i, p in players.items()}
            prices = robot.load_prices()
            adjust = standings.load_league().get("adjustments", [])
            goals = robot.goals_by_week(out["weeks"], adjust)
            own = robot.own_by_week(out["weeks"])

            # Each squad paired with the gameweeks it was picked for, so a
            # hover can say how many goals that pick actually returned.
            spans = []
            for gw, pick in out["per_week"].items():
                spans.append(([gw], pick))
            for gw, pick in out["value_per_week"].items():
                spans.append(([gw], pick))
            spans.append((out["weeks"], out["fixed"]))
            spans.append((out["weeks"], out["value_fixed"]))

            for span, pick in spans:
                if not pick:
                    continue
                detail = []
                for p in pick["picks"]:
                    detail.append({
                        "name": robot.name_of(p, names),
                        "code": None if p == robot.OWN else players[p]["code"],
                        "goals": sum(own.get(w, 0) if p == robot.OWN
                                     else goals.get(w, {}).get(p, 0) for w in span),
                        "price": robot.price_of(p, prices) if prices else None,
                        "club": "" if p == robot.OWN else players[p]["team_name"],
                        "source": "own" if p == robot.OWN else
                                  (prices or {}).get("price_source", {}).get(str(p), "pdf"),
                        "pens": None if p == robot.OWN else players[p].get("penalties_order"),
                        "status": None if p == robot.OWN else players[p].get("status"),
                        "news": None if p == robot.OWN else (players[p].get("news") or ""),
                    })
                detail.sort(key=lambda d: (-d["goals"], d["name"]))
                pick["detail"] = detail
                pick["names"] = [d["name"] for d in detail]

            body = json.dumps(out)
            ROBOT_CACHE[key] = body
            return self._send(body, "application/json")
        except Exception as exc:
            return self._fail(exc)

    def _insights(self):
        """Club records, squad value, transfer hindsight and generated facts."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        weeks = query.get("weeks", [""])[0]
        weeks = [int(w) for w in weeks.split(",") if w.strip().isdigit()] or None
        key = ("insights",) + tuple(weeks or ())
        if key in ROBOT_CACHE:
            return self._send(ROBOT_CACHE[key], "application/json")
        try:
            data = standings.build()
            span = weeks or data["played"]
            out = {
                "weeks": span,
                "clubs": insights.club_stats(span),
                "value": insights.squad_value(data, span),
                "transfers": insights.transfer_hindsight(data),
                "ownership": insights.ownership(data, span),
                "expected": insights.expected_goals(data, span),
                "palette": insights.palette(),
                "availability": insights.availability(),
                "upcoming": insights.upcoming(),
                "shape": insights.gameweek_shape(),
                "facts": [{"headline": h, "detail": d}
                          for h, d in insights.facts(data, span)],
            }
            body = json.dumps(out)
            ROBOT_CACHE[key] = body
            return self._send(body, "application/json")
        except Exception as exc:
            return self._fail(exc)

    def _refuse(self, errors):
        """A request the rules turned down: 400, with every reason."""
        body = json.dumps({"error": "; ".join(errors), "errors": errors}).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    @staticmethod
    def _window_context():
        """What every transfer check needs, read fresh from the API cache."""
        league = standings.load_league()
        prices = robot.load_prices()
        names = {p["id"]: p["web_name"] for p in fpl.bootstrap()["elements"]}
        deadlines = {gw: s["deadline"] for gw, s in fpl.gameweeks().items()}
        first = league["first_gameweek"]
        played = {gw for club in standings.conceded_by_club().values()
                  for gw in club if gw >= first}
        now = datetime.datetime.now(datetime.timezone.utc)
        return league, prices, names, deadlines, played, now

    def _window(self):
        """The open window, each manager's squad going into it, and the player list."""
        try:
            league, prices, names, deadlines, played, now = self._window_context()
            window = transfers.current_window(league)
            players = {p["id"]: p for p in fpl.players()}

            def describe(pick):
                if pick == transfers.OWN:
                    return {"id": transfers.OWN, "name": "Own Goals", "club": "",
                            "price": prices["own_goals"]}
                p = players.get(pick[0], {})
                return {"id": pick[0], "name": pick[1], "club": p.get("team_name", ""),
                        "code": p.get("code"), "price": prices["players"].get(pick[0])}

            managers = []
            if window:
                for name, m in league["managers"].items():
                    base = transfers.base_squad(m, window)
                    entry = next((s for s in m["squads"] if s["from"] == window["from"]), None)
                    managers.append({
                        "manager": name, "gk_club": m["gk_club"], "def_club": m["def_club"],
                        "budget": base.get("budget", m["budget"]) if base else None,
                        "squad": [describe(p) for p in base["picks"]] if base else [],
                        "entry": entry and {
                            "recorded": entry.get("recorded"),
                            "budget": entry.get("budget"),
                            "out": [describe(p)["name"] for p in entry.get("out", [])],
                            "in": [describe(p)["name"] for p in entry.get("in", [])],
                        },
                    })
            upcoming = sorted(gw for gw in deadlines
                              if not transfers.started(gw, deadlines, played, now))
            pool = [{"id": transfers.OWN, "name": "Own Goals", "club": "", "position": "",
                     "price": prices["own_goals"], "status": "a", "news": ""}]
            for pid, cost in prices["players"].items():
                p = players.get(pid)
                if p:
                    pool.append({"id": pid, "name": p["web_name"], "club": p["team_name"],
                                 "position": p["position"], "price": cost,
                                 "status": p["status"], "news": p.get("news") or ""})
            out = {
                "window": window,
                "deadline": window and deadlines.get(window["from"]),
                "closed_by_deadline": bool(window) and transfers.started(
                    window["from"], deadlines, played, now),
                "upcoming": upcoming[:6],
                "deadlines": {gw: deadlines[gw] for gw in upcoming[:6]},
                "cap": prices.get("budget"),
                "max_swaps": transfers.MAX_SWAPS,
                "managers": managers,
                "players": pool if window else [],
            }
            return self._send(json.dumps(out), "application/json")
        except Exception as exc:
            return self._fail(exc)

    def _change_window(self):
        """Organiser only: open a window for a gameweek, or close the open one."""
        try:
            req = self._body()
            with LEAGUE_LOCK:
                league, prices, names, deadlines, played, now = self._window_context()
                if req.get("action") == "open":
                    gw = req.get("gameweek")
                    if not isinstance(gw, int):
                        return self._refuse(["choose a gameweek"])
                    window = transfers.open_window(league, gw, deadlines, played, now)
                    event = "open"
                elif req.get("action") == "close":
                    window = transfers.close_window(league, now)
                    event = "close"
                else:
                    return self._refuse(["unknown action"])
                transfers.save(league)
                transfers.log({"event": event, "window": window["from"],
                               "at": now.isoformat(timespec="seconds")})
            return self._window()
        except transfers.TransferError as exc:
            return self._refuse(exc.errors)
        except Exception as exc:
            return self._fail(exc)

    def _transfer(self):
        """Record (or withdraw) one manager's swaps for the open window."""
        try:
            req = self._body()
            name = req.get("manager")
            with LEAGUE_LOCK:
                league, prices, names, deadlines, played, now = self._window_context()
                window = transfers.current_window(league)
                if req.get("withdraw"):
                    if name not in league["managers"]:
                        return self._refuse(["no manager called %r" % name])
                    transfers.withdraw(league, name, deadlines, played, now)
                    event = {"event": "withdraw", "manager": name}
                else:
                    entry, replaced = transfers.record(
                        league, name, req.get("out") or [], req.get("in") or [],
                        prices, names, deadlines, played, now)
                    event = {"event": "replace" if replaced else "transfer", "manager": name,
                             "out": entry["out"], "in": entry["in"],
                             "budget": entry["budget"], "picks": entry["picks"]}
                transfers.save(league)
                event.update({"window": window["from"], "at": now.isoformat(timespec="seconds")})
                transfers.log(event)
                # A squad changed, so every cached squad-derived answer is stale.
                ROBOT_CACHE.clear()
            return self._window()
        except transfers.TransferError as exc:
            return self._refuse(exc.errors)
        except Exception as exc:
            return self._fail(exc)

    def _asset(self, path):
        """Serve a crest or player photo, fetching it once into the cache."""
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[1] not in ASSETS or not parts[2].isdigit():
            return self.send_error(404)
        kind, code = parts[1], parts[2]
        url, content_type, ext = ASSETS[kind]
        local = os.path.join(ASSET_DIR, "%s-%s.%s" % (kind, code, ext))
        if not os.path.exists(local):
            try:
                req = urllib.request.Request(url % code,
                                             headers={"User-Agent": "fpl-league/0.1"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    blob = resp.read()
            except Exception:
                return self.send_error(404)
            os.makedirs(ASSET_DIR, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=ASSET_DIR, suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            os.replace(tmp, local)
        with open(local, "rb") as fh:
            blob = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "max-age=604800")
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html")) as fh:
                return self._send(fh.read(), "text/html; charset=utf-8")
        if path == "/api/standings":
            return self._table(fpl.CACHE_TTL)
        if path == "/api/robot":
            return self._robot()
        if path == "/api/insights":
            return self._insights()
        if path == "/api/window":
            return self._window()
        if path.startswith("/asset/"):
            return self._asset(path)
        self.send_error(404)

    def do_POST(self):
        # ttl=0 ignores the cache entirely, which is the point of the button.
        if self.path == "/api/refresh":
            ROBOT_CACHE.clear()
            return self._table(0)
        if self.path == "/api/window":
            return self._change_window()
        if self.path == "/api/transfer":
            return self._transfer()
        self.send_error(404)

    def log_message(self, fmt, *args):
        # Quiet the per-asset noise, but keep API calls and anything that
        # went wrong. log_error passes an int code first, so stringify.
        line = fmt % args
        if "/api/" in line or "code 4" in line or "code 5" in line:
            sys.stderr.write("  %s\n" % line)


if __name__ == "__main__":
    url = "http://127.0.0.1:%d" % PORT
    print("FPL league table on %s  (ctrl-c to stop)" % url)
    webbrowser.open(url)
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
