#!/usr/bin/env python3
"""
watcher.py
Cross-platform Git→Docker Compose redeployer with:
- .env secrets (auto-discovered)
- dynamic paths (no hardcoded host paths)
- optional auto-discovery of stacks
- Docker readiness wait (backoff)
- queued deploys + retries
- Discord + email notifications
- Step logging (LOG_STEPS)
- Loud startup diagnostics & "no changes" heartbeat
- Fail-fast validation for explicit projects
"""

import os, re, sys, time, json, random, socket, traceback, subprocess, smtplib, shutil
from pathlib import Path
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Dict, List, Optional, Tuple
import yaml
from dotenv import load_dotenv

# ---------- logging helpers

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def host() -> str:
    return socket.gethostname()

LOG_STEPS = str(os.environ.get("LOG_STEPS", "false")).lower() in ("true", "1", "yes")

def log(msg: str, step: bool = False) -> None:
    """High-level logs always; step logs only if LOG_STEPS enabled."""
    if step and not LOG_STEPS:
        return
    print(f"[{now()}] {msg}", flush=True)

def run(cmd: str, cwd: Optional[Path] = None, check: bool = True, capture: bool = True) -> str:
    log(f"RUN: {cmd} (cwd={cwd})", step=True)
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        shell=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        env=os.environ.copy(),
    )
    if check and result.returncode != 0:
        output = result.stdout or ""
        raise RuntimeError(f"command failed [{result.returncode}]… {cmd}\n{output}")
    return result.stdout if capture else ""

# ---------- notifications

def send_discord(webhook: str, message: str) -> None:
    if not webhook:
        return
    try:
        import urllib.request
        data = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        log("Sent Discord notification", step=True)
    except Exception:
        # never crash on notify
        pass

def _split_csv(val: str) -> List[str]:
    if not val:
        return []
    return [p.strip() for p in str(val).split(",") if p and p.strip()]

def send_email(cfg: dict, subject: str, body: str) -> None:
    if not cfg or str(cfg.get("enabled", "")).lower() not in ("true", "1", "yes"):
        return
    tos = _split_csv(cfg.get("to", ""))
    if not tos:
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(tos)
    msg["Date"] = formatdate(localtime=True)
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"])) as s:
        if str(cfg.get("starttls", "true")).lower() in ("true", "1", "yes"):
            s.starttls()
        if cfg.get("username"):
            s.login(cfg["username"], cfg["password"])
        s.sendmail(cfg["from"], tos, msg.as_string())
    log("Sent email notification", step=True)

# ---------- docker helpers

def docker_cli_name() -> str:
    try:
        out = run("docker compose version")
        if out:
            return "docker compose"
    except Exception:
        pass
    if shutil.which("docker-compose"):
        return "docker-compose"
    return "docker compose"

def wait_for_docker(max_wait_seconds: int = 600) -> bool:
    log("Waiting for Docker engine", step=True)
    start = time.time()
    delay = 2.0
    while True:
        try:
            out = run("docker info", check=True)
            if "Server Version" in out or "Storage Driver" in out:
                return True
        except Exception:
            pass
        if time.time() - start >= max_wait_seconds:
            return False
        time.sleep(min(delay, 20) + random.uniform(0, 0.75))
        delay *= 1.7

def compose_up(stack_dir: Path, compose_file: str) -> None:
    cli = docker_cli_name()
    base = f'{cli} -f "{compose_file}"'
    log(f"Compose up in {stack_dir} using {compose_file}", step=True)
    run(f"{base} pull --quiet", cwd=stack_dir, check=False)
    run(f"{base} up -d --remove-orphans", cwd=stack_dir)

def compose_up_with_retry(stack_dir: Path, compose_file: str, total_timeout: int = 300) -> None:
    start = time.time()
    attempt = 0
    while True:
        attempt += 1
        try:
            compose_up(stack_dir, compose_file)
            return
        except Exception:
            if time.time() - start > total_timeout:
                raise
            log(f"Retry compose_up attempt {attempt}", step=True)
            time.sleep(min(5 + attempt, 20))

# ---------- git helpers

def ensure_repo(repo_url: str, branch: str, worktree: Path) -> None:
    if not worktree.exists():
        log(f"Cloning {repo_url} into {worktree}")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        run(f'git clone --branch {branch} --single-branch "{repo_url}" "{worktree}"')
    else:
        try:
            current = run("git remote get-url origin", cwd=worktree).strip()
            if current != repo_url:
                log(f"Updating remote URL for {worktree}", step=True)
                run(f'git remote set-url origin "{repo_url}"', cwd=worktree)
        except Exception:
            pass

def fetch_origin(cwd: Path) -> None:
    log(f"Fetching origin in {cwd}", step=True)
    run("git fetch --prune origin", cwd=cwd)

def head_hash(cwd: Path, ref: str) -> str:
    return run(f"git rev-parse {ref}", cwd=cwd).strip()

def reset_to_origin(cwd: Path, branch: str) -> None:
    log(f"Resetting {cwd} to origin/{branch}", step=True)
    run(f"git reset --hard origin/{branch}", cwd=cwd)

# ---------- config & discovery

ENV_ONLY_PATTERN = re.compile(r"^\$\{([^}]+)\}$")
COMPOSE_FILENAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")

def interpolate_env(obj):
    if isinstance(obj, dict):
        return {k: interpolate_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate_env(x) for x in obj]
    if isinstance(obj, str):
        m = ENV_ONLY_PATTERN.match(obj)
        if m:
            return os.environ.get(m.group(1), "")
        # replace inline ${VAR} too
        return re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), ""), obj)
    return obj

def discover_stacks(root: Path, max_depth: int = 2, excludes: Optional[List[str]] = None) -> List[Tuple[Path, str]]:
    excludes = set(excludes or [".git", ".github", ".gitea", ".vscode", "__pycache__"])
    results: List[Tuple[Path, str]] = []

    def walk(dir_path: Path, depth: int):
        if depth > max_depth:
            return
        try:
            for child in dir_path.iterdir():
                name = child.name
                if name in excludes:
                    continue
                if child.is_dir():
                    for fname in COMPOSE_FILENAMES:
                        fpath = child / fname
                        if fpath.exists():
                            results.append((child, fname))
                            break
                    walk(child, depth + 1)
        except PermissionError:
            pass

    walk(root, 0)
    # de-dup by directory
    dedup: Dict[str, Tuple[Path, str]] = {}
    for d, f in results:
        dedup[str(d)] = (d, f)
    return list(dedup.values())

def default_base_dir() -> Path:
    p = os.environ.get("HOMELAB_BASE_DIR")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".homelab_watcher"

def resolve_env_file(cfg_path: Optional[Path]) -> Path:
    """
    Resolution order:
      1) HOMELAB_ENV_FILE
      2) .env beside the config file (if provided)
      3) .env beside this script
      4) .env in current working directory
      5) ~/.homelab_watcher/.env
    """
    # 1) explicit
    env_override = os.environ.get("HOMELAB_ENV_FILE")
    if env_override:
        return Path(env_override).expanduser()

    # 2) beside config
    if cfg_path:
        candidate = cfg_path.parent / ".env"
        if candidate.exists():
            return candidate

    # 3) beside script
    script_dir = Path(__file__).resolve().parent
    candidate = script_dir / ".env"
    if candidate.exists():
        return candidate

    # 4) cwd
    candidate = Path.cwd() / ".env"
    if candidate.exists():
        return candidate

    # 5) fallback
    return default_base_dir() / ".env"

def validate_projects(projects: List[dict]) -> None:
    for p in projects:
        name = p.get("name", "<unnamed>")
        repo_url = p.get("repo_url", "")
        path = p.get("path", "")
        # If user provided an explicit projects[] block, require repo_url + path
        if repo_url == "" or path == "":
            raise SystemExit(
                f"FATAL: project '{name}' in watcher.yaml is missing repo_url or path.\n"
                "       Either fill them, or remove the projects[] block and use implicit .env mode."
            )

# ---------- main

def main():
    # config path
    if len(sys.argv) >= 2:
        cfg_path = Path(sys.argv[1]).expanduser().resolve()
    else:
        cfg_path = Path(os.environ.get("HOMELAB_CONFIG") or (default_base_dir() / "watcher.yml")).resolve()

    # env path (auto-discovered)
    env_file = resolve_env_file(cfg_path if cfg_path.exists() else None)
    load_dotenv(dotenv_path=str(env_file))

    # refresh step flag after .env
    global LOG_STEPS
    LOG_STEPS = str(os.environ.get("LOG_STEPS", "false")).lower() in ("true", "1", "yes")

    banner = [
        "=== homelab watcher starting ===",
        f" host:        {host()}",
        f" env file:    {env_file}",
        f" config path: {cfg_path if cfg_path.exists() else '<not found>'}",
        f" log steps:   {LOG_STEPS}",
    ]
    for line in banner:
        print(line, flush=True)

    # load config or run implicit
    projects: List[dict] = []
    poll = int(os.environ.get("POLL_INTERVAL_SECONDS", 20))

    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cfg = interpolate_env(cfg)
        poll = int(cfg.get("poll_interval_seconds", poll))
        notify = cfg.get("notify", {}) or {}
        discord_webhook = notify.get("discord_webhook", os.environ.get("DISCORD_WEBHOOK", ""))
        email_cfg = notify.get("email", {
            "enabled": os.environ.get("EMAIL_ENABLED", "false"),
            "smtp_host": os.environ.get("EMAIL_SMTP_HOST", ""),
            "smtp_port": os.environ.get("EMAIL_SMTP_PORT", "587"),
            "username": os.environ.get("EMAIL_USERNAME", ""),
            "password": os.environ.get("EMAIL_PASSWORD", ""),
            "from": os.environ.get("EMAIL_FROM", "homelab-watcher@local"),
            "to": os.environ.get("EMAIL_TO", ""),
            "starttls": os.environ.get("EMAIL_STARTTLS", "true"),
        })
        projects = cfg.get("projects", [])
        if projects:
            validate_projects(projects)
    else:
        # implicit single-project from env
        discord_webhook = os.environ.get("DISCORD_WEBHOOK", "")
        email_cfg = {
            "enabled": os.environ.get("EMAIL_ENABLED", "false"),
            "smtp_host": os.environ.get("EMAIL_SMTP_HOST", ""),
            "smtp_port": os.environ.get("EMAIL_SMTP_PORT", "587"),
            "username": os.environ.get("EMAIL_USERNAME", ""),
            "password": os.environ.get("EMAIL_PASSWORD", ""),
            "from": os.environ.get("EMAIL_FROM", "homelab-watcher@local"),
            "to": os.environ.get("EMAIL_TO", ""),
            "starttls": os.environ.get("EMAIL_STARTTLS", "true"),
        }

    if not projects:
        # IMPLICIT MODE – driven by .env
        projects = [{
            "name": os.environ.get("PROJECT_NAME", "homelab"),
            "repo_url": os.environ.get("REPO_URL", ""),
            "branch": os.environ.get("BRANCH", "main"),
            "path": os.environ.get("WORKTREE_PATH", str(default_base_dir() / "worktrees" / "homelab")),
            # stacks omitted => auto-discover
        }]

    print(" projects:", flush=True)
    for p in projects:
        print(f"  - {p.get('name')}  repo={p.get('repo_url')}  branch={p.get('branch')}  path={p.get('path')}", flush=True)

    last_seen: Dict[str, str] = {}
    pending: Dict[str, str] = {}
    last_docker_warn_ts = 0

    send_discord(discord_webhook, f"homelab watcher started on {host()} at {now()}")

    while True:
        # docker readiness
        ready = wait_for_docker(max_wait_seconds=int(os.environ.get("DOCKER_READY_TIMEOUT", 120)))
        if not ready:
            if time.time() - last_docker_warn_ts > 300:
                msg = f"Docker not ready on {host()} at {now()}… will retry"
                log(msg)
                send_discord(discord_webhook, msg)
                send_email(email_cfg, "[homelab] docker not ready", msg)
                last_docker_warn_ts = time.time()
            time.sleep(poll)
            continue

        for proj in projects:
            name = proj["name"]
            try:
                repo_url = proj.get("repo_url", "")
                branch = proj.get("branch", "main")
                worktree = Path(proj.get("path")).expanduser().resolve()

                if not repo_url:
                    raise SystemExit(f"FATAL: project '{name}' has empty repo_url (fix .env or watcher.yaml).")
                if not worktree:
                    raise SystemExit(f"FATAL: project '{name}' has empty path (fix .env or watcher.yaml).")

                ensure_repo(repo_url, branch, worktree)
                fetch_origin(worktree)
                local = head_hash(worktree, "HEAD")
                remote = head_hash(worktree, f"origin/{branch}")

                if last_seen.get(name) is None:
                    last_seen[name] = local

                needs_deploy = pending.get(name) or (remote != local)

                if needs_deploy:
                    pending[name] = remote or "pending"
                    reset_to_origin(worktree, branch)

                    # stacks explicit or auto-discover
                    stacks_cfg = proj.get("stacks", [])
                    stacks: List[Tuple[Path, str]] = []
                    if stacks_cfg:
                        for s in stacks_cfg:
                            d = worktree / s.get("dir", ".")
                            fname = s.get("compose", "")
                            if not fname:
                                for c in COMPOSE_FILENAMES:
                                    if (d / c).exists():
                                        fname = c
                                        break
                            if fname and (d / fname).exists():
                                stacks.append((d, fname))
                    else:
                        stacks = discover_stacks(
                            worktree,
                            max_depth=int(os.environ.get("DISCOVERY_DEPTH", 2))
                        )

                    if stacks:
                        for d, fname in stacks:
                            log(f"[{name}] stack: {d} / {fname}", step=True)
                            compose_up_with_retry(d, fname, total_timeout=int(os.environ.get("COMPOSE_TIMEOUT", 300)))
                    else:
                        log(f"[{name}] no compose stacks found… skipping deploy")

                    msg = f"[{name}] deployed {(remote or 'unknown')[:7]} on {host()} at {now()}… was {(local or 'unknown')[:7]}"
                    log(msg)
                    send_discord(discord_webhook, msg)
                    send_email(email_cfg, f"[homelab] {name} updated", msg)
                    last_seen[name] = remote or local
                    pending.pop(name, None)
                else:
                    log(f"[{name}] no changes (local={local[:7]} remote={remote[:7]})", step=False)

            except SystemExit as se:
                # hard fail config issues so you see it fast
                print(str(se), flush=True)
                return
            except Exception as e:
                tb = traceback.format_exc()
                err = f"[{name}] deployment failed at {now()} on {host()}… {e}\n{tb}"
                log(err)
                send_discord(discord_webhook, err[:1900])
                send_email(email_cfg, f"[homelab] {name} deployment failed", err)

        time.sleep(poll)

if __name__ == "__main__":
    main()
