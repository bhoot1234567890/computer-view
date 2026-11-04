# Third-party components

Computer View's own code is covered by LICENSE. Dependencies and external tools retain their own licenses; consult the exact versions you distribute.

- aiohttp: Python HTTP/WebSocket runtime dependency.
- noVNC: downloaded separately from the pinned commit recorded in `src/computer_view/assets.py`; checksum verified. Retain the upstream license files when distributing downloaded assets.
- cloudflared: optional, separately installed tunnel executable, not bundled.
- Chromium, TigerVNC, OpenSSL, NSS/certutil and desktop components: separately provisioned by the operator, not bundled.

Build artifacts do not bundle browser binaries, noVNC assets, bot credentials or live desktop state. Review upstream notices and redistribution requirements before distributing any larger combined image.
