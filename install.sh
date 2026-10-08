#!/bin/sh
# Downloaded bootstrap code is verified before execution. No sudo/profile edits.
set -eu
if [ "$#" -lt 2 ]; then
    echo 'Usage: sh install.sh BOOTSTRAP_HTTPS_URL BOOTSTRAP_SHA256 [bootstrap options]' >&2
    exit 2
fi
url=$1
hash=$2
shift 2
python=${SCRIPTKIT_PYTHON:-python3}
if ! command -v "$python" >/dev/null 2>&1; then
    echo 'Python 3.11+ is required. Install Python with venv/ensurepip, then rerun.' >&2
    exit 1
fi
# -I ignores PYTHONPATH and user site; URL fetching and hashing need only stdlib.
exec "$python" -I -X utf8 -c '
import hashlib, pathlib, runpy, sys, tempfile, urllib.parse, urllib.request
url, digest, *args = sys.argv[1:]
if sys.version_info < (3, 11):
    sys.exit("Python 3.11+ with venv/ensurepip required")
if urllib.parse.urlsplit(url).scheme != "https":
    sys.exit("bootstrap URL must use HTTPS")
with urllib.request.urlopen(url, timeout=60) as response:
    if urllib.parse.urlsplit(response.url).scheme != "https":
        sys.exit("insecure bootstrap redirect")
    raw = response.read(1024 * 1024 + 1)
if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
    sys.exit("bootstrap SHA-256 mismatch or oversized script")
with tempfile.TemporaryDirectory(prefix="scriptkit-bootstrap-") as directory:
    path = pathlib.Path(directory) / "bootstrap.py"
    path.write_bytes(raw)
    sys.argv = [str(path), *args]
    runpy.run_path(str(path), run_name="__main__")
' "$url" "$hash" "$@"
