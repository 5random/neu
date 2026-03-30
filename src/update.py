from __future__ import annotations
from pathlib import Path
from typing import Optional, Callable, Tuple
import subprocess
import shutil
import sys
import os
import time
import hashlib
import re
from src.config import get_logger

def _determine_root() -> Path:
    """
    Determine project root robustly:
    1) Environment override: CVD_ROOT or ROOT
    2) CLI override: --cvd-root=PATH or --root=PATH (if present in sys.argv)
    3) Walk upwards from this file preferring .git, then pyproject.toml, then config/config.yaml,
       and finally (requirements.txt + README.md) as a last resort.
    Raises RuntimeError if no root can be determined.
    """
    # 1) Environment overrides
    for var in ('CVD_ROOT', 'ROOT'):
        val = os.environ.get(var)
        if val:
            p = Path(val).expanduser().resolve()
            if p.exists() and p.is_dir():
                return p

    # 2) CLI overrides
    for arg in sys.argv:
        if arg.startswith('--cvd-root='):
            p = Path(arg.split('=', 1)[1]).expanduser().resolve()
            if p.exists() and p.is_dir():
                return p
        if arg.startswith('--root='):
            p = Path(arg.split('=', 1)[1]).expanduser().resolve()
            if p.exists() and p.is_dir():
                return p

    # 3) Marker-based upward search (prefer strong markers)
    start_dir = Path(__file__).resolve().parent
    chain = [start_dir] + list(start_dir.parents)

    # a) .git (Best marker)
    for d in chain:
        if (d / '.git').exists():
            return d
    # b) pyproject.toml
    for d in chain:
        if (d / 'pyproject.toml').exists():
            return d
    # c) config/config.yaml (avoid collision with src/config package)
    for d in chain:
        if (d / 'config' / 'config.yaml').exists():
            return d
    # d) fallback: both requirements.txt and README.md
    for d in chain:
        if (d / 'requirements.txt').exists() and (d / 'README.md').exists():
            return d

    raise RuntimeError('Could not determine project ROOT. Set CVD_ROOT/ROOT environment variable or provide --cvd-root/--root CLI option.')

ROOT = _determine_root()
LOG_DIR = ROOT / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
UPDATE_LOG = LOG_DIR / 'update.log'
logger = get_logger("update")

def _run(cmd: list[str], cwd: Optional[Path] = None, timeout: float = 120.0) -> Tuple[int, str, str]:
    """Run a subprocess command with sane defaults and timeout.

    Returns (returncode, stdout, stderr). On timeout, returns code 124 with message in stderr.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"

def _log_to_file(msg: str) -> None:
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    with UPDATE_LOG.open('a', encoding='utf-8') as f:
        f.write(f'[{ts}] {msg}\n')

def _emit(progress: Optional[Callable[[str], None]], msg: str) -> None:
    _log_to_file(msg)
    logger.info(msg)
    if progress:
        try:
            progress(msg)
        except Exception as e:
            # Do not break update flow due to UI callback issues
            logger.error(f'Progress callback failed: {e}')


def _read_text_file(path: Path) -> str:
    with path.open('r', encoding='utf-8') as f:
        return f.read()


def _logical_requirements_lines(content: str) -> list[str]:
    """Return logical requirements lines with line continuations joined."""
    logical_lines: list[str] = []
    current = ""

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if current:
            current = f"{current} {stripped}".strip()
        else:
            current = stripped

        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue

        if current:
            logical_lines.append(current)
        current = ""

    if current:
        logical_lines.append(current)

    return logical_lines


def _verify_requirements_file(
    req_path: Path,
    progress: Optional[Callable[[str], None]],
) -> Tuple[bool, bool]:
    """Verify requirements integrity before dependency installation.

    Returns ``(is_valid, use_hashes)``.
    Accepted verification modes:
    - Exact SHA-256 match via ``CVD_REQUIREMENTS_SHA256``
    - Pip-compatible hash pinning on every installable requirement line
    """
    if not req_path.exists():
        _emit(progress, f"Requirements file not found: {req_path}")
        return False, False

    try:
        content = _read_text_file(req_path)
    except Exception as e:
        _emit(progress, f"Error reading requirements file: {e}")
        return False, False

    expected_sha256 = str(os.environ.get('CVD_REQUIREMENTS_SHA256', '') or '').strip().lower()
    file_sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest().lower()

    if expected_sha256:
        if file_sha256 != expected_sha256:
            _emit(
                progress,
                'Requirements checksum verification failed: '
                f'expected {expected_sha256}, got {file_sha256}.',
            )
            return False, False
        _emit(progress, 'requirements.txt verified via trusted SHA-256 checksum.')
        return True, False

    requirement_lines: list[str] = []
    missing_hashes: list[str] = []

    for line in _logical_requirements_lines(content):
        if not line or line.startswith('#'):
            continue
        if line.startswith(('-r ', '--requirement ', '-c ', '--constraint ')):
            _emit(progress, f'Unsupported nested requirements directive in {req_path.name}: {line}')
            return False, False
        if line.startswith(('-e ', '--editable ')):
            _emit(progress, f'Editable installs not supported with hash verification in {req_path.name}: {line}')
            return False, False
        if line.startswith('-'):
            # Global pip options are allowed but do not count as installable requirements.
            continue

        requirement_lines.append(line)
        if '--hash=' not in line:
            missing_hashes.append(line)

    if not requirement_lines:
        _emit(progress, f'No installable requirements found in {req_path.name}.')
        return False, False

    if missing_hashes:
        _emit(
            progress,
            'Requirements verification failed: every dependency must be hash-pinned '
            'or CVD_REQUIREMENTS_SHA256 must match the full file.',
        )
        return False, False

    _emit(progress, 'requirements.txt verified via per-package hashes.')
    return True, True

def get_local_commit_short() -> str:
    code, out, _ = _run(['git', 'rev-parse', '--short', 'HEAD'])
    return out.strip() if code == 0 and out.strip() else 'unknown'

def _ensure_git_available() -> None:
    """Raise if git is not available in PATH."""
    if shutil.which('git') is None:
        raise RuntimeError('git executable not found in PATH.')

def _get_upstream() -> Tuple[Optional[str], Optional[str]]:
    """Return (upstream_ref, remote_name) for current HEAD, if any."""
    u_code, u_out, _ = _run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])
    if u_code != 0 or not u_out.strip():
        return None, None
    upstream_ref = u_out.strip()
    remote_name = upstream_ref.split('/', 1)[0] if '/' in upstream_ref else 'origin'
    return upstream_ref, remote_name

def check_update() -> dict:
    # Ensure git exists and identify upstream/remote; verify that specific remote and fetch only it
    _ensure_git_available()

    upstream_ref, remote_name = _get_upstream()
    if remote_name:
        _verify_remote(progress=None, raise_on_fail=True, remote_name=remote_name)
        remote_to_fetch = remote_name
    else:
        # Fall back to origin
        _verify_remote(progress=None, raise_on_fail=True, remote_name='origin')
        remote_to_fetch = 'origin'

    # Fetch and hard-fail on errors so callers can notify the user
    code, out, err = _run(['git', 'fetch', remote_to_fetch, '--prune'])
    if code != 0:
        msg = f'git fetch {remote_to_fetch} failed: {err.strip() or out.strip() or "unknown error"}'
        logger.error(msg)
        raise RuntimeError(msg)

    ahead, behind = 0, 0
    remote_short = ''

    # Use previously detected upstream ref (handles detached HEAD or missing upstream)
    if upstream_ref:
        # ahead/behind relative to upstream
        c_code, c_out, _ = _run(['git', 'rev-list', '--left-right', '--count', f'HEAD...{upstream_ref}'])
        if c_code == 0 and c_out.strip():
            parts = c_out.strip().split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                ahead, behind = int(parts[0]), int(parts[1])
        # short hash of upstream
        r_code, r_out, _ = _run(['git', 'rev-parse', '--short', upstream_ref])
        if r_code == 0 and r_out.strip():
            remote_short = r_out.strip()
    # else: no upstream; keep defaults

    return {'ahead': ahead, 'behind': behind, 'local': get_local_commit_short(), 'remote': remote_short}

def _backup_config() -> Optional[Path]:
    cfg = ROOT / 'config' / 'config.yaml'
    if cfg.exists():
        bkp = cfg.with_suffix('.yaml.bak')
        try:
            shutil.copy2(cfg, bkp)
            return bkp
        except Exception as e:
            logger.warning(f'Failed to backup config: {e}')
            return None
    return None

def _restore_config(bkp: Optional[Path]) -> None:
    if not bkp:
        return
    cfg = ROOT / 'config' / 'config.yaml'
    try:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if bkp.exists():
            shutil.copy2(bkp, cfg)
    except Exception as e:
        logger.warning(f'Failed to restore config from backup: {e}')

def perform_update(progress: Optional[Callable[[str], None]] = None) -> bool:
    if not (ROOT / '.git').exists():
        _emit(progress, 'No .git found; update not available.')
        return False

    # Ensure remote is trusted before reaching out to network
    try:
        # Verify the actual upstream remote if available; fall back to origin
        upstream_ref, remote_name = _get_upstream()
        target_remote = remote_name or 'origin'
        if not _verify_remote(progress=progress, raise_on_fail=True, remote_name=target_remote):
            _emit(progress, 'Aborting update due to unverified or missing git remote.')
            return False
    except Exception as e:
        _emit(progress, f'Aborting update: remote verification failed: {e}')
        return False

    _emit(progress, 'Fetching latest changes...')
    try:
        status = check_update()
    except Exception as e:
        _emit(progress, f'Failed to check for updates: {e}')
        logger.error(f'check_update failed: {e}')
        return False
    _emit(progress, f"Local {status.get('local')} | Remote {status.get('remote') or 'n/a'} | behind={status.get('behind', 0)}")

    if status.get('behind', 0) <= 0:
        _emit(progress, 'Already up to date.')
        return True

    # Prevent pull on dirty worktree to avoid failures or unintended merges
    st_code, st_out, st_err = _run(['git', 'status', '--porcelain'])
    if st_code == 0 and st_out.strip():
        _emit(progress, 'Uncommitted local changes detected; aborting pull. Commit or stash them and retry.')
        return False

    _emit(progress, 'Backing up config/config.yaml ...')
    bkp = _backup_config()

    _emit(progress, 'Pulling changes (fast-forward only)...')
    code, _, err = _run(['git', 'pull', '--ff-only'])
    if code != 0:
        logger.error(f'git pull failed: {err.strip()}')
        _emit(progress, f'git pull failed: {err.strip()}')
        _restore_config(bkp)
        return False

    try:
        _restore_config(bkp)
        _emit(progress, 'Config preserved.')
        # Apply skip-worktree only if the file is tracked in git
        try:
            ls_code, ls_out, ls_err = _run(['git', 'ls-files', '--error-unmatch', 'config/config.yaml'])
            if ls_code == 0:
                up_code, up_out, up_err = _run(['git', 'update-index', '--skip-worktree', 'config/config.yaml'])
                if up_code == 0:
                    _emit(progress, 'Marked config/config.yaml as skip-worktree (keeps local changes on future pulls).')
                else:
                    logger.error(f'Failed to mark skip-worktree: {up_err.strip() or up_out.strip()}')
                    _emit(progress, f'Error: could not set skip-worktree on config/config.yaml: {up_err.strip() or up_out.strip()}')
            else:
                msg = 'config/config.yaml is not tracked in git; skip-worktree not applied.'
                logger.info(msg)
                _emit(progress, msg)
        except Exception as ex:
            logger.error(f'Unexpected error while setting skip-worktree: {ex}')
            _emit(progress, f'Warning: skip-worktree step encountered an error: {ex}')
    except Exception as e:
        logger.warning(f'Failed to restore or protect config: {e}')
        _emit(progress, f'Warning: failed to restore config: {e}')

    req = ROOT / 'requirements.txt'
    if req.exists():
        # Configurable opt-out via environment variable
        skip_auto_deps = str(os.environ.get('CVD_SKIP_AUTO_DEPS', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
        if skip_auto_deps:
            warn_msg = 'Skipping automatic dependency update (CVD_SKIP_AUTO_DEPS enabled).'
            logger.warning(warn_msg)
            _emit(progress, warn_msg)
        else:
            # Warn that auto-updates are enabled
            logger.warning('Automatic dependency updates are enabled. Proceeding only after requirements verification.')
            _emit(progress, 'Verifying requirements.txt before installing dependencies...')

            ok, use_hashes = _verify_requirements_file(req, progress)
            if not ok:
                # Verification failed: emit explicit security warning and abort
                sec_msg = 'SECURITY WARNING: requirements verification failed; aborting dependency installation.'
                logger.error(sec_msg)
                _emit(progress, sec_msg)
                return False

            _emit(progress, 'Installing/updating dependencies via pip...')
            code, out, err = _run([sys.executable, '-m', 'pip', 'install', '-U', 'pip', 'setuptools', 'wheel'])
            if code != 0:
                logger.warning(f'Failed to upgrade pip/setuptools/wheel: {err.strip() or out.strip()}')
            else:
                _emit(progress, out.strip() or err.strip())

            pip_cmd = [sys.executable, '-m', 'pip', 'install', '-r', str(req)]
            if use_hashes:
                pip_cmd.append('--require-hashes')

            code, out, err = _run(pip_cmd)
            if code != 0:
                logger.error(f'pip install failed: {err.strip()}')
                _emit(progress, f'pip install failed: {err.strip()}')
                return False
            _emit(progress, 'Dependencies installed.')
    else:
        _emit(progress, 'No requirements.txt found; skipping dependency install.')

    _emit(progress, 'Update finished.')
    return True

def _get_remote_url(name: str = 'origin') -> Tuple[bool, str]:
    """Return (ok, url_or_err)."""
    code, out, err = _run(['git', 'remote', 'get-url', name])
    if code == 0 and out.strip():
        return True, out.strip()
    return False, (err.strip() or 'unknown remote')

def _verify_remote(progress: Optional[Callable[[str], None]] = None, raise_on_fail: bool = False, remote_name: str = 'origin') -> bool:
    """
    Verify that the configured git remote URL is trusted before network operations.

    Trust policy (configurable via env):
    - CVD_EXPECTED_REMOTE: exact URL string required (if set)
    - CVD_ALLOWED_REMOTES: regex patterns separated by commas/semicolons; any match is accepted
      Defaults to allow only the known repository 'github.com[:/]5random/neu(.git)?'.
    On failure: log and emit an explicit error. If raise_on_fail is True, raise RuntimeError.
    """
    ok, url = _get_remote_url(remote_name)
    if not ok:
        msg = f'Failed to read git remote URL for {remote_name}: {url}'
        logger.error(msg)
        _emit(progress, msg)
        if raise_on_fail:
            raise RuntimeError(msg)
        return False

    expected_exact = os.environ.get('CVD_EXPECTED_REMOTE', '').strip()
    allowed_env = os.environ.get('CVD_ALLOWED_REMOTES', '').strip()

    # If an exact URL is configured, enforce it strictly
    if expected_exact:
        if url != expected_exact:
            msg = f'SECURITY ERROR: Git remote URL "{url}" does not match expected "{expected_exact}".'
            logger.error(msg)
            _emit(progress, msg)
            if raise_on_fail:
                raise RuntimeError(msg)
            return False
        _emit(progress, f'Git remote verified ({remote_name}): {url}')
        return True

    # Otherwise fall back to allowlist patterns
    patterns = []
    if allowed_env:
        for part in re.split(r'[;,]', allowed_env):
            p = part.strip()
            if p:
                patterns.append(p)
    else:
        patterns = [r'^git@github\.com:5random/neu(\.git)?$', r'^https://github\.com/5random/neu(\.git)?$']

    trusted = False
    for pat in patterns:
        try:
            if re.search(pat, url, re.IGNORECASE):
                trusted = True
                break
        except re.error:
            logger.warning(f'Ignoring invalid regex in CVD_ALLOWED_REMOTES: {pat}')

    if not trusted:
        msg = f'SECURITY ERROR: Git remote URL "{url}" is not in the trusted allowlist; refusing to update.'
        logger.error(msg)
        _emit(progress, msg)
        if raise_on_fail:
            raise RuntimeError(msg)
        return False
    return True

def _cleanup_before_exec() -> None:
    """Best-effort cleanup before replacing the process image.
    - Flush stdio
    - Close all logging handlers
    - Attempt to join non-daemon threads briefly
    All steps are wrapped in try/except to avoid blocking the restart.
    """
    try:
        logger.info('Preparing to restart: flushing streams and closing resources...')
    except Exception:
        pass

    # Flush stdio
    try:
        if hasattr(sys.stdout, 'flush'):
            sys.stdout.flush()
    except Exception:
        pass
    try:
        if hasattr(sys.stderr, 'flush'):
            sys.stderr.flush()
    except Exception:
        pass

    # Close all logging handlers (root and children)
    try:
        import logging
        loggers = []
        try:
            # Collect all known loggers
            mgr = logging.Logger.manager
            for name, obj in getattr(mgr, 'loggerDict', {}).items():
                if isinstance(obj, logging.Logger):
                    loggers.append(obj)
        except Exception:
            pass
        # Ensure root and our module logger are included
        loggers.extend([logging.getLogger(), logger])
        seen = set()
        for lg in loggers:
            if lg in seen:
                continue
            seen.add(lg)
            for h in getattr(lg, 'handlers', [])[:]:
                try:
                    h.flush()
                except Exception:
                    pass
                try:
                    h.close()
                except Exception:
                    pass
                try:
                    lg.removeHandler(h)
                except Exception:
                    pass
    except Exception:
        pass

    # Attempt to join non-daemon threads briefly
    try:
        import threading
        current = threading.current_thread()
        for t in threading.enumerate():
            if t is current:
                continue
            if t.daemon:
                continue
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
    except Exception:
        pass

def restart_self(exec_args: Optional[list[str]] = None) -> None:
    # Self-restart without root/systemd
    # Loggen und gesamten Prozess inkl. Skript ersetzen
    logger.info('Restarting application process...')
    args = exec_args if exec_args is not None else sys.argv
    try:
        _cleanup_before_exec()
    except Exception as e:
        # Non-fatal: proceed with exec regardless
        try:
            logger.error(f'Cleanup before restart encountered an error: {e}')
        except Exception:
            pass
    finally:
        # Always replace the process image
        os.execv(sys.executable, [sys.executable] + args)
