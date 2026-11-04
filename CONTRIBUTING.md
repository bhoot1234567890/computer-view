# Contributing

Use Python 3.11 or newer on Linux. Install openssl and libnss3-tools for real NSS integration tests.

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install -e '.[dev]'
    python -m unittest discover -s tests -v
    ruff check src tests
    python -m build

Write a failing regression test before changing behavior. Never include live tokens, cookies, IDs, private keys or deployment state. Tests use temporary local fixtures, not the production desktop. Run a real HTTPS/WSS/RFB verification in an isolated deployment before release; unit tests do not prove the public tunnel works. Verify both wheel and source archive installation.
