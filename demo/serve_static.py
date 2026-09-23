"""Serve a built front end for the demo, with an index.html fallback.

    python -m demo.serve_static dashboard/dist 5173
    python -m demo.serve_static client-portal/dist 5174

**Why not `npm run dev`.** Two Vite dev servers are the heaviest thing in this
demo by a wide margin, and on a laptop already holding a browser and Docker they
are what gets killed first. A production build served statically is lighter,
starts instantly, and is closer to what would actually be deployed.

**Why not `python -m http.server`.** It 404s on `/track/<token>`, because that
path exists only in the client-side router - so the recipient's page, which is
the last beat of the demo, would not load. A static host in production does this
same fallback.

**Run these in your own terminals.** They are long-lived processes and should
not be children of anything that might tidy up after itself.
"""
from __future__ import annotations

import functools
import http.server
import os
import socketserver
import sys


class SinglePageApp(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the stdlib spells it this way
        path = self.translate_path(self.path)
        if (not os.path.exists(path) or os.path.isdir(path)) and not self.path.startswith(
            "/assets"
        ):
            # Anything that is not a real file is a client-side route. `/assets`
            # is excluded so a genuinely missing bundle 404s loudly rather than
            # returning HTML that the browser then fails to parse as JavaScript.
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    directory, port = sys.argv[1], int(sys.argv[2])
    if not os.path.isdir(directory):
        print(
            f"{directory} does not exist - build it first:\n"
            f"  VITE_API_BASE_URL=http://<your LAN ip>:8000 npm run build",
            file=sys.stderr,
        )
        return 1

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(
        ("0.0.0.0", port), functools.partial(SinglePageApp, directory=directory)
    ) as server:
        print(f"serving {directory} on http://0.0.0.0:{port}")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
