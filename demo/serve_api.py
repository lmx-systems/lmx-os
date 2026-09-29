"""Run the API on one socket that answers IPv4 *and* IPv6.

    python -m demo.serve_api            # port 8000
    python -m demo.serve_api --port 9000

**Why this exists.** The iOS Simulator resolves `localhost` to `::1`, and
`uvicorn --host 0.0.0.0` listens on IPv4 only, so the driver app got
`Connection refused` on every request:

    nw_endpoint_flow_failed_with_error [C1.1.1 IPv6#....8000 ... interface: lo0]
    Socket SO_ERROR [61: Connection refused]

`--host ::` fixes the app and breaks everything else, because macOS sets
`IPV6_V6ONLY` on by default: the browsers and `demo/run_full_loop.py` all reach
the API over IPv4 and then cannot.

**And the app cannot be pointed somewhere else.** `ServerScreen` lives behind
sign-in, and sign-in needs the API - so an app that cannot reach a server cannot
be told where one is. `src/api/serverUrl.ts` records the same trap from the
other side: *"the first Android build installed, launched, and could not sign
in."*

So the socket is built here with `IPV6_V6ONLY` cleared and handed to uvicorn,
which is the one arrangement where `localhost` means the same thing to the
simulator, a browser, and a script.

**Development only.** It reads whatever `DATABASE_URL` and friends the
environment gives it, exactly as `uvicorn app.main:app` would; nothing about
production listens this way, because there a load balancer decides.
"""
from __future__ import annotations

import argparse
import socket

import uvicorn


def dual_stack_socket(host: str, port: int) -> socket.socket:
    """One AF_INET6 socket that also accepts IPv4, as `::ffff:a.b.c.d`."""
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # The whole point. Default is 1 on macOS, which is what splits the two
    # stacks and makes `localhost` mean different things to different clients.
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="::", help="IPv6 bind address; :: is any")
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args()

    sock = dual_stack_socket(args.host, args.port)
    print(
        f"LMX OS on [::]:{args.port} and 0.0.0.0:{args.port} - one socket.\n"
        f"  simulator / browser  http://localhost:{args.port}\n"
        f"  scripts              http://127.0.0.1:{args.port}"
    )
    config = uvicorn.Config("app.main:app", log_level=args.log_level)
    uvicorn.Server(config).run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
