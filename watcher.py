#!/usr/bin/env python3
"""
watcher.py
Cross-platform Git→Docker Compose redeployer with:
- .env secrets
- dynamic paths
- auto-discovery of stacks
- Docker readiness wait
- queued deploys + retries
- Discord + email notifications
- Step logging (toggle with LOG_STEPS)
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

# global toggle (default false)
LOG_STEPS = str(os.environ.get("LOG_STEPS", "false")).lower() in ("true", "1", "yes")

def log(msg: str, step: bool = False) -> None:
    """Log high-level always, step-level only if enabled."""
    if step and not LOG_STEPS:
        return
    line = f"[{now()}] {msg}"
    print(line, flush=True)

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
        pass

def send_email(cfg: dict, subject: str, body: str) -> None:
    if not cfg or str(cfg.get("enabled", "")).lower() not in ("true", "1", "yes"):
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    tos = _split_csv(cfg.get("to", ""))
    if not tos:
        return
    msg["To"] = ", ".join(tos)
    msg["Date"] = formatdate(localtime=True)
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"])) as s:
        if str(cfg.get("starttls", "true")).lower() in ("true", "1", "yes"):
            s.starttls()
        if cfg.get("username"):
            s.login(cfg["username"], cfg["password"])
        s.sendmail(cfg["from"], tos, msg.as_string())
    log("Sent email notification", step=True)

def _split_csv(val: str) -> List[str]:
    if not val:
        return []
    return [p.strip() for p in val.split(",") if p.strip()]

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
        except Exception as e:
            if time.time() - start > total_timeout:
                raise
            log(f"Retry compose_up attempt {attempt}", step=True)
            time.sleep(min(5 + attempt, 20))

# ---------- git helpers

def ensure_repo(repo_url: str, branch: str, worktree: Path) -> None:
    if not worktree.exists():
        log(f"Cloning {repo_url} into {worktree}", step=True)
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

# ---------- config and discovery

ENV_ONLY_PATTERN = re.compile(r"^\$\{([^}]+)\}$")

def interpolate_env(obj):
    if isinstance(obj, dict):
        return {k: interpolate_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate_env(x) for x in obj]
    if isinstance(obj, str):
        m = ENV_ONLY_PATTERN.match(obj)
        if m:
            return os.environ.get(m.group(1), "")
        # also support inline ${VAR} occurrences
        def repl(match):
            return os.environ.get(match.group(1), "")
        return re.sub(r"\$\{([^}]+)\}", repl, obj)
    return obj

COMPOSE_FILENAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")

def discover_stacks(root: Path, max_depth: int = 2, excludes: Optional[List[str]] = None) -> List[Tuple[Path, str]]:
    """
    Search for compose files within root up to max_depth.
    Returns list of tuples: (stack_dir, compose_filename)
    """
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
                    # check for compose files here
                    for fname in COMPOSE_FILENAMES:
                        fpath = child / fname
                        if fpath.exists():
                            results.append((child, fname))
                            break
                    # continue deeper
                    walk(child, depth + 1)
        except PermissionError:
            pass

    walk(root, 0)
    # de-dup by directory
    dedup: Dict[str, Tuple[Path, str]] = {}
    for d, f in results:
        dedup[str(d)] = (d, f)
    return list(dedup.values())

# ---------- main

def default_base_dir() -> Path:
    # HOMELAB_BASE_DIR … else ~/.homelab
    p = os.environ.get("HOMELAB_BASE_DIR")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".homelab"

def main():
    # Load .env
    env_file = os.environ.get("HOMELAB_ENV_FILE") or (default_base_dir() / ".env")
    load_dotenv(dotenv_path=str(env_file))

    # Config path… CLI arg wins, else HOMELAB_CONFIG, else base_dir/watcher.yml
    if len(sys.argv) >= 2:
        cfg_path = Path(sys.argv[1]).expanduser()
    else:
        cfg_path = Path(os.environ.get("HOMELAB_CONFIG") or (default_base_dir() / "watcher.yml"))

    if not cfg_path.exists():
        sys.stderr.write(f"Config not found: {cfg_path}\n")
        sys.exit(2)

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = interpolate_env(cfg)

    poll = int(cfg.get("poll_interval_seconds", os.environ.get("POLL_INTERVAL_SECONDS", 20)))
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
    if not projects:
        # single-project implicit mode… use current folder as repo
        projects = [{
            "name": os.environ.get("PROJECT_NAME", "project"),
            "repo_url": os.environ.get("REPO_URL", ""),
            "branch": os.environ.get("BRANCH", "main"),
            "path": os.environ.get("WORKTREE_PATH", str(default_base_dir() / "worktrees" / "project")),
            # stacks: omit for auto-discovery
        }]

    last_seen: Dict[str, str] = {}
    pending: Dict[str, str] = {}
    last_docker_warn_ts = 0

    send_discord(discord_webhook, f"homelab watcher started on {host()} at {now()}")

    while True:
        # Docker readiness gate per loop
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
            try:
                name = proj["name"]
                repo_url = proj.get("repo_url", "")
                branch = proj.get("branch", "main")
                worktree = Path(proj.get("path", str(default_base_dir() / "worktrees" / name))).expanduser()

                if repo_url:
                    ensure_repo(repo_url, branch, worktree)
                    fetch_origin(worktree)
                    local = head_hash(worktree, "HEAD")
                    remote = head_hash(worktree, f"origin/{branch}")
                else:
                    # no remote url… treat current tree as immutable source
                    local = remote = head_hash(worktree, "HEAD") if (worktree / ".git").exists() else "unknown"

                if last_seen.get(name) is None:
                    last_seen[name] = local

                needs_deploy = pending.get(name) or (repo_url and remote != local)

                if needs_deploy:
                    pending[name] = remote or "pending"
                    if repo_url:
                        reset_to_origin(worktree, branch)

                    # figure stacks… explicit list or auto-discover
                    stacks_cfg = proj.get("stacks", [])
                    stacks: List[Tuple[Path, str]] = []
                    if stacks_cfg:
                        for s in stacks_cfg:
                            d = worktree / s.get("dir", ".")
                            fname = s.get("compose", "")
                            if not fname:
                                # choose first matching compose file if not specified
                                for c in COMPOSE_FILENAMES:
                                    if (d / c).exists():
                                        fname = c
                                        break
                            if fname and (d / fname).exists():
                                stacks.append((d, fname))
                    else:
                        stacks = discover_stacks(worktree, max_depth=int(os.environ.get("DISCOVERY_DEPTH", 2)))

                    if not stacks:
                        log(f"[{name}] no compose stacks found… skipping deploy")
                    else:
                        for d, fname in stacks:
                            compose_up_with_retry(d, fname, total_timeout=int(os.environ.get("COMPOSE_TIMEOUT", 300)))

                    msg = f"[{name}] deployed { (remote or 'unknown')[:7] } on {host()} at {now()}… was { (local or 'unknown')[:7] }"
                    log(msg)
                    send_discord(discord_webhook, msg)
                    send_email(email_cfg, f"[homelab] {name} updated", msg)
                    last_seen[name] = remote or local
                    pending.pop(name, None)

            except Exception as e:
                tb = traceback.format_exc()
                err = f"[{proj.get('name','?')}] deployment failed at {now()} on {host()}… {e}\n{tb}"
                log(err)
                send_discord(discord_webhook, err[:1900])
                send_email(email_cfg, f"[homelab] {proj.get('name','?')} deployment failed", err)

        time.sleep(poll)

if __name__ == "__main__":
    main()
