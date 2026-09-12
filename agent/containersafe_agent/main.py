"""Entrypoint: `containersafe-agent` — loads config, runs the
reconnecting WebSocket client forever. Deployed as its own small Docker
container (see docker-compose.yml) — needs nothing on the host beyond
/var/run/docker.sock, unlike FiresLog's systemd-based agent, which needs
host filesystem/journal access for its different job (log tailing).
"""

import asyncio
import logging

from . import wsclient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    asyncio.run(wsclient.run())


if __name__ == "__main__":
    main()
