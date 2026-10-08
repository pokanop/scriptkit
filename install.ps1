# No execution-policy changes, admin privileges or profile mutation.
param(
    [Parameter(Mandatory=$true)][string]$BootstrapUrl,
    [Parameter(Mandatory=$true)][string]$BootstrapSha256,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$BootstrapArgs
)
$ErrorActionPreference = 'Stop'
$python = if ($env:SCRIPTKIT_PYTHON) { $env:SCRIPTKIT_PYTHON } else { 'python' }
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine('Python 3.11+ with venv/ensurepip is required. Install Python, then rerun.')
    exit 1
}
$code = @'
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
'@
& $python -I -c $code $BootstrapUrl $BootstrapSha256 @BootstrapArgs
exit $LASTEXITCODE
