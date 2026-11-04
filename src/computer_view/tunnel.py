"""Optional Cloudflare adapter. The core works with any suitable HTTPS reverse proxy."""
import fcntl
import json
import os
import re
import shutil
import subprocess
from computer_view.config import Config, validate_origin


def capture_origin(line: str, config: Config):
    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com(?![a-zA-Z0-9.-])', line)
    if not match:
        return None
    origin = validate_origin(match.group(0))
    path = config.state_dir / 'public-url.txt'
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(origin + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)
    return origin


def run_tunnel(config: Config, binary='cloudflared'):
    executable = shutil.which(binary)
    if not executable:
        raise FileNotFoundError('Install cloudflared first, or use your own HTTPS reverse proxy')
    if not config.http_socket.is_socket():
        raise RuntimeError('Start computer-view serve before starting a tunnel')
    lock_path = config.state_dir / 'tunnel.lock'
    with lock_path.open('a') as lock:
        lock_path.chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A Computer View tunnel is already running') from None
        ingress = config.state_dir / 'quick-tunnel.json'
        ingress.write_text(json.dumps({'ingress': [{'service': 'unix:' + str(config.http_socket)}]}))
        ingress.chmod(0o600)
        command = [executable, 'tunnel', '--no-autoupdate', '--config', str(ingress),
                   '--url', 'unix:' + str(config.http_socket)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        origin = None
        try:
            for line in process.stdout:
                found = capture_origin(line, config)
                if found:
                    origin = found
                print(line.rstrip(), flush=True)
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()
            saved = config.state_dir / 'public-url.txt'
            if origin and saved.exists() and saved.read_text().strip() == origin:
                saved.unlink()
