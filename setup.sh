#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -----------------------------------------------------------------------------
# User-configurable settings
# -----------------------------------------------------------------------------
ENV_NAME="${ENV_NAME:-cvd-tracker}"
CONDA_CHANNEL="${CONDA_CHANNEL:-conda-forge}"
CLONE_URL="${CLONE_URL:-https://github.com/5random/neu.git}"
CLONE_DIR="${CLONE_DIR:-$SCRIPT_DIR}"
PORT="${PORT:-8080}"
SERVICE_NAME="${SERVICE_NAME:-cvd_tracker}"
AUTO_UPDATE="${AUTO_UPDATE:-1}"
SYSTEM_UPGRADE="${SYSTEM_UPGRADE:-1}"

# Access configuration:
#   direct access           -> app is reached directly on the Pi via http://<host>:<port>/
#   external reverse proxy  -> an external HTTPS proxy forwards requests to the Pi
USE_REVERSE_PROXY="${USE_REVERSE_PROXY:-auto}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-}"
ROOT_PATH="${ROOT_PATH:-}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"
WEBSITE_URL_SOURCE="${WEBSITE_URL_SOURCE:-auto}"
SESSION_COOKIE_HTTPS_ONLY="${SESSION_COOKIE_HTTPS_ONLY:-auto}"
INTERACTIVE_SETUP="${INTERACTIVE_SETUP:-auto}"

# Raspberry Pi OS settings
LOCALE_VALUE="${LOCALE_VALUE:-}"
TIMEZONE_VALUE="${TIMEZONE_VALUE:-}"
KEYBOARD_LAYOUT="${KEYBOARD_LAYOUT:-}"
WIFI_COUNTRY="${WIFI_COUNTRY:-}"
HOSTNAME_VALUE="${HOSTNAME_VALUE:-}"
ENABLE_SSH="${ENABLE_SSH:-1}"
SSH_PASSWORD_LOGIN="${SSH_PASSWORD_LOGIN:-1}"
EXPAND_ROOTFS="${EXPAND_ROOTFS:-0}"

# micromamba paths
MICROMAMBA_BIN_DEFAULT="${HOME}/.local/bin/micromamba"
MICROMAMBA_BIN="${MICROMAMBA_BIN:-$MICROMAMBA_BIN_DEFAULT}"
MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-}"

ENV_FILE=""
RUN_USER=""
USER_HOME=""
PRIMARY_IPV4=""
ROOT_PATH_NORMALIZED=""

msg() {
  echo
  echo "[setup] $*"
}

warn() {
  echo
  echo "[setup] WARNING: $*" >&2
}

die() {
  echo
  echo "[setup] ERROR: $*" >&2
  exit 1
}

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

is_interactive_session() {
  case "${INTERACTIVE_SETUP,,}" in
    1|true|yes|on)
      return 0
      ;;
    0|false|no|off)
      return 1
      ;;
    auto|"")
      [[ -t 0 && -t 1 ]]
      ;;
    *)
      warn "Unknown INTERACTIVE_SETUP='${INTERACTIVE_SETUP}', falling back to auto detection."
      [[ -t 0 && -t 1 ]]
      ;;
  esac
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

normalize_root_path() {
  local value
  value="$(trim "$1")"
  if [[ -z "$value" || "$value" == "/" ]]; then
    printf '%s' ""
    return
  fi
  if [[ "$value" != /* ]]; then
    value="/$value"
  fi
  value="${value%/}"
  printf '%s' "$value"
}

normalize_public_base_url_input() {
  local value
  value="$(trim "$1")"
  if [[ -z "$value" ]]; then
    printf '%s' ""
    return
  fi
  if [[ "$value" != http://* && "$value" != https://* ]]; then
    value="https://${value}"
  fi
  if [[ "$value" != */ ]]; then
    value="${value}/"
  fi
  printf '%s' "$value"
}

build_direct_base_url() {
  local value
  value="$(trim "$1")"
  if [[ -z "$value" ]]; then
    printf '%s' ""
    return
  fi
  if [[ "$value" == http://* || "$value" == https://* ]]; then
    printf '%s' "$(normalize_public_base_url_input "$value")"
    return
  fi
  printf 'http://%s:%s/' "$value" "$PORT"
}

prompt_with_default() {
  local prompt="$1"
  local default_value="${2:-}"
  local reply=""

  if [[ -n "$default_value" ]]; then
    read -r -p "${prompt} [${default_value}]: " reply || true
    if [[ -z "$reply" ]]; then
      reply="$default_value"
    fi
  else
    read -r -p "${prompt}: " reply || true
  fi

  printf '%s' "$reply"
}

prompt_yes_no() {
  local prompt="$1"
  local default_answer="${2:-y}"
  local reply=""
  local hint=""

  case "${default_answer,,}" in
    y|yes|1|true|on) hint="Y/n" ;;
    n|no|0|false|off) hint="y/N" ;;
    *) hint="y/n" ;;
  esac

  while true; do
    read -r -p "${prompt} [${hint}]: " reply || true
    reply="$(trim "$reply")"
    if [[ -z "$reply" ]]; then
      reply="$default_answer"
    fi
    case "${reply,,}" in
      y|yes|1|true|on) return 0 ;;
      n|no|0|false|off) return 1 ;;
    esac
    echo "Please answer yes or no."
  done
}

prompt_for_port() {
  local candidate
  while true; do
    candidate="$(prompt_with_default "App-Port auf dem Pi" "$PORT")"
    candidate="$(trim "$candidate")"
    if [[ "$candidate" =~ ^[0-9]+$ ]] && (( candidate >= 1 && candidate <= 65535 )); then
      PORT="$candidate"
      return
    fi
    echo "Bitte einen Port zwischen 1 und 65535 angeben."
  done
}

prompt_for_remote_proxy_settings() {
  local default_proxy_ips raw_public_url default_root_path

  default_proxy_ips="$FORWARDED_ALLOW_IPS"
  default_root_path="$(normalize_root_path "$ROOT_PATH")"
  while true; do
    FORWARDED_ALLOW_IPS="$(prompt_with_default "IP-Adresse(n) des vorgeschalteten Reverse Proxy für gui.forwarded_allow_ips (Komma-getrennt)" "$default_proxy_ips")"
    FORWARDED_ALLOW_IPS="$(trim "$FORWARDED_ALLOW_IPS")"
    if [[ -n "$FORWARDED_ALLOW_IPS" ]]; then
      break
    fi
    echo "forwarded_allow_ips darf bei externem Reverse Proxy nicht leer sein."
  done

  while true; do
    raw_public_url="$(prompt_with_default "Öffentliche HTTPS-URL des Reverse Proxy (z.B. https://cvd.example.de/)" "$PUBLIC_BASE_URL")"
    PUBLIC_BASE_URL="$(normalize_public_base_url_input "$raw_public_url")"
    if [[ "$PUBLIC_BASE_URL" =~ ^https:// ]]; then
      break
    fi
    echo "Bitte eine HTTPS-URL angeben. Wenn du nur Hostname oder IP angibst, wird https:// automatisch ergänzt."
  done

  ROOT_PATH="$(prompt_with_default "Optionaler Unterpfad auf dem Reverse Proxy (leer oder z.B. /cvd)" "$default_root_path")"
  ROOT_PATH="$(normalize_root_path "$ROOT_PATH")"
  USE_REVERSE_PROXY="1"
  SESSION_COOKIE_HTTPS_ONLY="1"
}

prompt_for_direct_access_settings() {
  local default_host raw_host

  discover_primary_ipv4
  default_host="$PRIMARY_IPV4"
  raw_host="$(prompt_with_default "IP-Adresse oder Hostname des Pi für den direkten Zugriff" "$default_host")"
  raw_host="$(trim "$raw_host")"
  [[ -n "$raw_host" ]] || die "Für den Direktzugriff muss eine IP-Adresse oder ein Hostname angegeben werden."

  PUBLIC_BASE_URL="$(build_direct_base_url "$raw_host")"
  ROOT_PATH=""
  ROOT_PATH_NORMALIZED=""
  FORWARDED_ALLOW_IPS=""
  USE_REVERSE_PROXY="0"

  if [[ "$SESSION_COOKIE_HTTPS_ONLY" == "auto" ]]; then
    SESSION_COOKIE_HTTPS_ONLY="0"
  fi
}

prompt_for_access_settings() {
  local proxy_default="n"

  if ! is_interactive_session; then
    return
  fi

  if is_truthy "$USE_REVERSE_PROXY"; then
    proxy_default="y"
  fi

  msg "Interactive network setup"
  echo "Hinweis: In gui.forwarded_allow_ips gehören nur die IP-Adressen vertrauenswürdiger Reverse-Proxys,"
  echo "nicht die Browser-Clients und nicht die Pi-Zieladresse."

  prompt_for_port

  if prompt_yes_no "HTTPS über einen externen Reverse Proxy verwenden?" "$proxy_default"; then
    prompt_for_remote_proxy_settings
  else
    prompt_for_direct_access_settings
  fi
}

run_as_target_user() {
  sudo -H -u "$RUN_USER" env \
    HOME="$USER_HOME" \
    PATH="$USER_HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$@"
}

micromamba_exec() {
  run_as_target_user env \
    MAMBA_ROOT_PREFIX="$MAMBA_ROOT_PREFIX" \
    "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" "$@"
}

detect_user() {
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    RUN_USER="$SUDO_USER"
  else
    RUN_USER="${USER:-$(id -un)}"
  fi

  if USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"; then
    :
  else
    USER_HOME="$HOME"
  fi

  if [[ "$MICROMAMBA_BIN" == "$MICROMAMBA_BIN_DEFAULT" ]]; then
    MICROMAMBA_BIN="${USER_HOME}/.local/bin/micromamba"
  fi
  if [[ -z "$MAMBA_ROOT_PREFIX" ]]; then
    MAMBA_ROOT_PREFIX="${USER_HOME}/micromamba"
  fi

  export MAMBA_ROOT_PREFIX
}

ensure_supported_port() {
  if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
    die "PORT must be an integer in [1, 65535], got '$PORT'."
  fi
}

prepare_install_settings() {
  ROOT_PATH_NORMALIZED="$(normalize_root_path "$ROOT_PATH")"

  case "${USE_REVERSE_PROXY,,}" in
    auto|"")
      if [[ -n "$FORWARDED_ALLOW_IPS" ]]; then
        USE_REVERSE_PROXY="1"
      else
        USE_REVERSE_PROXY="0"
      fi
      ;;
    1|true|yes|on)
      USE_REVERSE_PROXY="1"
      ;;
    0|false|no|off)
      USE_REVERSE_PROXY="0"
      ;;
    *)
      die "USE_REVERSE_PROXY must be auto, true, or false."
      ;;
  esac

  if is_truthy "$USE_REVERSE_PROXY"; then
    [[ -n "$FORWARDED_ALLOW_IPS" ]] || die "FORWARDED_ALLOW_IPS is required when USE_REVERSE_PROXY=true."
    PUBLIC_BASE_URL="$(normalize_public_base_url_input "$PUBLIC_BASE_URL")"
    [[ "$PUBLIC_BASE_URL" =~ ^https:// ]] || die "PUBLIC_BASE_URL must be a HTTPS URL when USE_REVERSE_PROXY=true."
  else
    FORWARDED_ALLOW_IPS=""
    ROOT_PATH=""
    ROOT_PATH_NORMALIZED=""
    if [[ -n "$PUBLIC_BASE_URL" ]]; then
      PUBLIC_BASE_URL="$(build_direct_base_url "$PUBLIC_BASE_URL")"
    else
      discover_primary_ipv4
      [[ -n "$PRIMARY_IPV4" ]] || die "Could not determine a primary IPv4 address. Set PUBLIC_BASE_URL manually."
      PUBLIC_BASE_URL="http://${PRIMARY_IPV4}:${PORT}/"
    fi
  fi
}

require_64bit_arm() {
  local arch
  arch="$(uname -m)"
  case "$arch" in
    aarch64|arm64)
      msg "Detected 64-bit ARM: $arch"
      ;;
    *)
      warn "Detected architecture '$arch'. Raspberry Pi OS Lite 64-bit is strongly recommended for micromamba + OpenCV."
      ;;
  esac
}

discover_primary_ipv4() {
  PRIMARY_IPV4="$(
    ip -4 route get 1.1.1.1 2>/dev/null \
      | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}'
  )"
  if [[ -z "$PRIMARY_IPV4" ]]; then
    PRIMARY_IPV4="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
}

show_network_inventory() {
  discover_primary_ipv4
  msg "Network interfaces"
  ip -o -4 addr show scope global | awk '{print "  " $2 "  IPv4: " $4}'
  ip -o link show | awk -F': ' '$2 != "lo" {print $2}' | while read -r ifc; do
    local mac state
    mac="$(cat "/sys/class/net/$ifc/address" 2>/dev/null || true)"
    state="$(cat "/sys/class/net/$ifc/operstate" 2>/dev/null || true)"
    echo "  ${ifc}  MAC: ${mac}  STATE: ${state}"
  done
  if [[ -n "$PRIMARY_IPV4" ]]; then
    echo "  Primary IPv4: ${PRIMARY_IPV4}"
  fi
}

install_system_packages() {
  local packages=(
    git wget curl ca-certificates
    ufw openssh-server
    v4l-utils
    libgl1 libglib2.0-0
    python3
    raspi-config
  )

  msg "Updating apt metadata"
  sudo apt update

  if is_truthy "$SYSTEM_UPGRADE"; then
    msg "Upgrading Raspberry Pi OS packages"
    sudo apt full-upgrade -y
  fi

  msg "Installing system packages"
  sudo apt install -y --no-install-recommends "${packages[@]}"
}

configure_pi_os() {
  if ! command -v raspi-config >/dev/null 2>&1; then
    warn "raspi-config not found; skipping Raspberry Pi OS base configuration."
    return
  fi

  msg "Applying Raspberry Pi OS settings"

  if is_truthy "$ENABLE_SSH"; then
    sudo raspi-config nonint do_ssh 0 || true
  fi

  if [[ -n "$HOSTNAME_VALUE" ]]; then
    sudo raspi-config nonint do_hostname "$HOSTNAME_VALUE" || true
  fi
  if [[ -n "$LOCALE_VALUE" ]]; then
    sudo raspi-config nonint do_change_locale "$LOCALE_VALUE" || true
  fi
  if [[ -n "$TIMEZONE_VALUE" ]]; then
    sudo raspi-config nonint do_change_timezone "$TIMEZONE_VALUE" || true
  fi
  if [[ -n "$KEYBOARD_LAYOUT" ]]; then
    sudo raspi-config nonint do_configure_keyboard "$KEYBOARD_LAYOUT" || true
  fi
  if [[ -n "$WIFI_COUNTRY" ]]; then
    sudo raspi-config nonint do_wifi_country "$WIFI_COUNTRY" || true
  fi
  if is_truthy "$EXPAND_ROOTFS"; then
    sudo raspi-config nonint do_expand_rootfs || true
  fi
}

setup_ssh_server() {
  local file="/etc/ssh/sshd_config"
  local password_setting

  if is_truthy "$SSH_PASSWORD_LOGIN"; then
    password_setting="yes"
  else
    password_setting="no"
  fi

  msg "Configuring SSH server"
  sudo systemctl enable ssh

  if [[ -f "$file" && ! -f "${file}.bak" ]]; then
    sudo cp "$file" "${file}.bak"
  fi

  sudo sed -ri "s/^\s*#?\s*PasswordAuthentication\s+.*/PasswordAuthentication ${password_setting}/" "$file" || true
  grep -Eq "^\s*PasswordAuthentication\s+${password_setting}" "$file" || echo "PasswordAuthentication ${password_setting}" | sudo tee -a "$file" >/dev/null

  sudo sed -ri 's/^\s*#?\s*PermitRootLogin\s+.*/PermitRootLogin no/' "$file" || true
  grep -Eq '^\s*PermitRootLogin\s+no' "$file" || echo 'PermitRootLogin no' | sudo tee -a "$file" >/dev/null

  sudo sed -ri 's/^\s*#?\s*UsePAM\s+.*/UsePAM yes/' "$file" || true
  grep -Eq '^\s*UsePAM\s+yes' "$file" || echo 'UsePAM yes' | sudo tee -a "$file" >/dev/null

  sudo sed -ri 's/^\s*#?\s*PermitEmptyPasswords\s+.*/PermitEmptyPasswords no/' "$file" || true
  grep -Eq '^\s*PermitEmptyPasswords\s+no' "$file" || echo 'PermitEmptyPasswords no' | sudo tee -a "$file" >/dev/null

  sudo sed -ri 's/^\s*#?\s*X11Forwarding\s+.*/X11Forwarding no/' "$file" || true
  grep -Eq '^\s*X11Forwarding\s+no' "$file" || echo 'X11Forwarding no' | sudo tee -a "$file" >/dev/null

  sudo systemctl restart ssh || true
}

directory_is_empty() {
  local path="$1"
  [[ -d "$path" ]] || return 0
  [[ -z "$(find "$path" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]
}

ensure_repo_present() {
  if git -C "$CLONE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    msg "Repository already present in ${CLONE_DIR}"
    return
  fi

  if [[ -e "$CLONE_DIR" && ! -d "$CLONE_DIR" ]]; then
    die "CLONE_DIR exists but is not a directory: ${CLONE_DIR}"
  fi

  if [[ -d "$CLONE_DIR" && ! -d "$CLONE_DIR/.git" && ! directory_is_empty "$CLONE_DIR" ]]; then
    die "CLONE_DIR exists and is not an empty Git checkout: ${CLONE_DIR}"
  fi

  msg "Cloning repository into ${CLONE_DIR}"
  mkdir -p "$(dirname "$CLONE_DIR")"
  run_as_target_user git clone "$CLONE_URL" "$CLONE_DIR"
}

verify_repo() {
  local origin_url branch dirty ahead behind commit_short

  git -C "$CLONE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "${CLONE_DIR} is not a Git repository."

  origin_url="$(git -C "$CLONE_DIR" remote get-url origin 2>/dev/null || echo "")"
  branch="$(git -C "$CLONE_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"
  dirty="$(git -C "$CLONE_DIR" status --porcelain)"

  if [[ -n "$origin_url" && "$origin_url" != "$CLONE_URL" ]]; then
    warn "Origin URL differs from CLONE_URL: ${origin_url}"
  fi

  if ! git -C "$CLONE_DIR" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    if git -C "$CLONE_DIR" ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
      git -C "$CLONE_DIR" branch --set-upstream-to="origin/${branch}" "$branch" || true
    fi
  fi

  run_as_target_user git -C "$CLONE_DIR" fetch --all --prune || true
  read -r ahead behind <<<"$(git -C "$CLONE_DIR" rev-list --left-right --count HEAD...@{u} 2>/dev/null || echo "0 0")"

  msg "Repository status: branch=${branch} ahead=${ahead:-0} behind=${behind:-0} dirty=$([[ -n "$dirty" ]] && echo yes || echo no)"

  if [[ -n "$dirty" ]]; then
    warn "Local changes detected; automatic Git update skipped."
  elif [[ "${behind:-0}" -gt 0 && is_truthy "$AUTO_UPDATE" ]]; then
    msg "Updating repository with fast-forward pull"
    run_as_target_user git -C "$CLONE_DIR" pull --ff-only || warn "Fast-forward pull failed; please review manually."
  fi

  commit_short="$(git -C "$CLONE_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  msg "Current commit: ${commit_short}"
}

install_micromamba() {
  if command -v micromamba >/dev/null 2>&1; then
    MICROMAMBA_BIN="$(command -v micromamba)"
    msg "Using micromamba from PATH: ${MICROMAMBA_BIN}"
    return
  fi

  if [[ -x "$MICROMAMBA_BIN" ]]; then
    msg "Using existing micromamba binary: ${MICROMAMBA_BIN}"
    return
  fi

  msg "Installing micromamba for user ${RUN_USER}"
  run_as_target_user bash -lc "curl -Ls https://micro.mamba.pm/install.sh | bash"

  [[ -x "$MICROMAMBA_BIN" ]] || die "micromamba binary not found after installation: ${MICROMAMBA_BIN}"

  micromamba_exec shell init -s bash -r "$MAMBA_ROOT_PREFIX" >/dev/null 2>&1 || true
}

mamba_env_exists() {
  [[ -d "${MAMBA_ROOT_PREFIX}/envs/${ENV_NAME}" ]]
}

find_env_file() {
  local candidates=(
    "${CLONE_DIR}/enviroment.yaml"
    "${CLONE_DIR}/environment.yml"
    "${CLONE_DIR}/environment.yaml"
  )

  ENV_FILE=""
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      ENV_FILE="$candidate"
      break
    fi
  done

  if [[ -n "$ENV_FILE" ]]; then
    msg "Using environment file: ${ENV_FILE}"
  else
    warn "No environment file found; using fallback package list."
  fi
}

create_mamba_env() {
  find_env_file

  if [[ -n "$ENV_FILE" ]]; then
    if mamba_env_exists; then
      msg "Updating environment ${ENV_NAME}"
      micromamba_exec env update -n "$ENV_NAME" -f "$ENV_FILE"
    else
      msg "Creating environment ${ENV_NAME}"
      micromamba_exec env create -n "$ENV_NAME" -f "$ENV_FILE"
    fi
  else
    if mamba_env_exists; then
      msg "Environment ${ENV_NAME} already exists"
    else
      msg "Creating fallback environment ${ENV_NAME}"
      micromamba_exec create -y -n "$ENV_NAME" -c "$CONDA_CHANNEL" \
        python=3.11 \
        opencv numpy pillow pyyaml python-dateutil \
        fastapi uvicorn requests pytest \
        pip wheel setuptools
    fi
  fi
}

install_python_requirements() {
  local filter_pkgs tmp_req

  [[ -f "$CLONE_DIR/requirements.txt" ]] || die "requirements.txt not found in ${CLONE_DIR}"

  find_env_file
  micromamba_exec run -n "$ENV_NAME" python -m pip install -U pip setuptools wheel

  filter_pkgs="opencv-python|numpy|pillow|pyyaml|python-dateutil|fastapi|uvicorn|requests|pytest"
  if [[ -n "$ENV_FILE" ]] && grep -qiE '(^|\s|-)nicegui' "$ENV_FILE"; then
    filter_pkgs="${filter_pkgs}|nicegui"
  fi

  tmp_req="$(mktemp)"
  sed -E '/^\s*($|#)/d' "$CLONE_DIR/requirements.txt" \
    | { grep -Eiv "^\s*(${filter_pkgs})(\s*[<>=!~]=.*)?\s*$" || true; } \
    > "$tmp_req"

  if [[ -s "$tmp_req" ]]; then
    msg "Installing pip-only requirements"
    micromamba_exec run -n "$ENV_NAME" python -m pip install -r "$tmp_req"
  else
    msg "No additional pip-only packages required"
  fi

  rm -f "$tmp_req"
}

ensure_runtime_dirs() {
  msg "Ensuring runtime directories exist"
  run_as_target_user mkdir -p \
    "$CLONE_DIR/logs" \
    "$CLONE_DIR/data/history" \
    "$CLONE_DIR/alerts"
}

resolve_app_bind_host() {
  printf '%s' "0.0.0.0"
}

resolve_configured_website_url() {
  if [[ -n "$PUBLIC_BASE_URL" ]]; then
    printf '%s' "$PUBLIC_BASE_URL"
    return
  fi
  if [[ -n "$PRIMARY_IPV4" ]]; then
    printf '%s' "http://${PRIMARY_IPV4}:${PORT}/"
    return
  fi
  printf '%s' ""
}

resolve_website_url_source() {
  case "$WEBSITE_URL_SOURCE" in
    auto)
      if [[ -n "$PUBLIC_BASE_URL" ]]; then
        printf '%s' "config"
      else
        printf '%s' "runtime_persist"
      fi
      ;;
    config|runtime|runtime_persist)
      printf '%s' "$WEBSITE_URL_SOURCE"
      ;;
    *)
      die "WEBSITE_URL_SOURCE must be auto, config, runtime, or runtime_persist."
      ;;
  esac
}

resolve_session_cookie_https_only() {
  case "${SESSION_COOKIE_HTTPS_ONLY,,}" in
    auto)
      if [[ "$PUBLIC_BASE_URL" =~ ^https:// ]]; then
        printf '%s' "true"
      else
        printf '%s' "false"
      fi
      ;;
    1|true|yes|on)
      printf '%s' "true"
      ;;
    0|false|no|off)
      printf '%s' "false"
      ;;
    *)
      die "SESSION_COOKIE_HTTPS_ONLY must be auto/true/false."
      ;;
  esac
}

backup_config() {
  local config_path backup_path
  config_path="${CLONE_DIR}/config/config.yaml"
  [[ -f "$config_path" ]] || return
  backup_path="${config_path}.bak.$(date +%Y%m%d_%H%M%S)"
  cp "$config_path" "$backup_path"
  msg "Backed up config to ${backup_path}"
}

configure_application() {
  local app_host website_url_source website_url https_only forwarded_ips reverse_proxy_enabled

  discover_primary_ipv4
  app_host="$(resolve_app_bind_host)"
  website_url_source="$(resolve_website_url_source)"
  website_url="$(resolve_configured_website_url)"
  https_only="$(resolve_session_cookie_https_only)"
  reverse_proxy_enabled="false"
  forwarded_ips="127.0.0.1"
  if is_truthy "$USE_REVERSE_PROXY"; then
    reverse_proxy_enabled="true"
    forwarded_ips="${FORWARDED_ALLOW_IPS}"
  fi

  backup_config

  msg "Applying application configuration"
  cat <<'PY' | (
    cd "$CLONE_DIR"
    micromamba_exec run -n "$ENV_NAME" python - \
      "config/config.yaml" \
      "$app_host" \
      "$PORT" \
      "$reverse_proxy_enabled" \
      "$forwarded_ips" \
      "$ROOT_PATH_NORMALIZED" \
      "$website_url_source" \
      "$website_url" \
      "$https_only"
  )
from __future__ import annotations

import sys

from src.config import load_config, save_config


def normalize_root_path(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "/":
        return ""
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/")


def normalize_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value if value.endswith("/") else value + "/"


config_path, host, port_raw, reverse_proxy_raw, forwarded_allow_ips, root_path_raw, website_url_source, website_url_raw, https_only_raw = sys.argv[1:]
cfg = load_config(config_path)

cfg.gui.host = host
cfg.gui.port = int(port_raw)
cfg.gui.auto_open_browser = False
cfg.gui.reverse_proxy_enabled = reverse_proxy_raw.lower() == "true"
cfg.gui.forwarded_allow_ips = (forwarded_allow_ips or "127.0.0.1").strip()
cfg.gui.root_path = normalize_root_path(root_path_raw)
cfg.gui.session_cookie_https_only = https_only_raw.lower() == "true"

cfg.email.website_url_source = website_url_source
normalized_url = normalize_url(website_url_raw)
if normalized_url:
    cfg.email.website_url = normalized_url

save_config(cfg, config_path)
print(
    f"Configured host={cfg.gui.host} port={cfg.gui.port} "
    f"reverse_proxy_enabled={cfg.gui.reverse_proxy_enabled} "
    f"forwarded_allow_ips={cfg.gui.forwarded_allow_ips!r} "
    f"root_path={cfg.gui.root_path!r} "
    f"website_url_source={cfg.email.website_url_source!r} "
    f"website_url={cfg.email.website_url!r}"
)
PY
}

csv_to_lines() {
  printf '%s' "$1" \
    | tr ',' '\n' \
    | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' \
    | sed '/^$/d'
}

setup_firewall() {
  msg "Configuring UFW"
  sudo ufw default deny incoming || true
  sudo ufw default allow outgoing || true
  sudo ufw allow OpenSSH || sudo ufw allow 22/tcp || true
  sudo ufw limit ssh || true

  if is_truthy "$USE_REVERSE_PROXY"; then
    if [[ "$FORWARDED_ALLOW_IPS" == "*" ]]; then
      sudo ufw allow "${PORT}/tcp" || true
    else
      while read -r proxy_ip; do
        sudo ufw allow from "$proxy_ip" to any port "$PORT" proto tcp || true
      done < <(csv_to_lines "$FORWARDED_ALLOW_IPS")
    fi
  else
    sudo ufw allow "${PORT}/tcp" || true
  fi

  sudo ufw --force enable || true
  sudo ufw status verbose || true
}

ensure_user_video_group() {
  msg "Adding ${RUN_USER} to video group"
  sudo usermod -aG video "$RUN_USER" || true
}

setup_systemd_service() {
  local service_path
  service_path="/etc/systemd/system/${SERVICE_NAME}.service"

  msg "Creating systemd service ${SERVICE_NAME}"
  sudo tee "$service_path" >/dev/null <<EOF
[Unit]
Description=CVD-Tracker service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
SupplementaryGroups=video
WorkingDirectory=${CLONE_DIR}
Environment=MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX}
Environment=PYTHONUNBUFFERED=1
Environment=OMP_NUM_THREADS=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=MKL_NUM_THREADS=1
Environment=NUMEXPR_NUM_THREADS=1
ExecStart=${MICROMAMBA_BIN} --root-prefix ${MAMBA_ROOT_PREFIX} run -n ${ENV_NAME} python ${CLONE_DIR}/main.py --config ${CLONE_DIR}/config/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}.service"
}

start_services() {
  msg "Starting services"
  sudo systemctl enable --now "${SERVICE_NAME}.service"
}

print_hints() {
  local guessed_url

  discover_primary_ipv4
  guessed_url="${PUBLIC_BASE_URL}"

  echo
  echo "Setup complete."
  if is_truthy "$USE_REVERSE_PROXY"; then
    echo "  Access mode: external HTTPS reverse proxy"
  else
    echo "  Access mode: direct HTTP access on the Pi"
  fi
  echo "  Repo: ${CLONE_DIR}"
  echo "  Service: sudo systemctl status ${SERVICE_NAME}"
  echo "  Logs: sudo journalctl -u ${SERVICE_NAME} -f"
  if [[ -n "$guessed_url" ]]; then
    echo "  Expected app URL: ${guessed_url}"
  fi
  if is_truthy "$USE_REVERSE_PROXY"; then
    echo "  Trusted proxy IPs: ${FORWARDED_ALLOW_IPS}"
  fi
  echo "  Port on Pi: ${PORT}"
  show_network_inventory
  echo "  Re-run full setup: bash setup.sh full"
}

full_setup() {
  detect_user
  prompt_for_access_settings
  ensure_supported_port
  prepare_install_settings
  require_64bit_arm
  install_system_packages
  configure_pi_os
  setup_ssh_server
  ensure_repo_present
  verify_repo
  install_micromamba
  create_mamba_env
  install_python_requirements
  ensure_runtime_dirs
  configure_application
  ensure_user_video_group
  setup_firewall
  setup_systemd_service
  start_services
  print_hints
}

show_usage() {
  cat <<EOF
Usage:
  bash setup.sh full
  bash setup.sh network
  bash setup.sh repo
  bash setup.sh system
  bash setup.sh pi-config
  bash setup.sh ssh
  bash setup.sh micromamba
  bash setup.sh env
  bash setup.sh pip
  bash setup.sh app-config
  bash setup.sh firewall
  bash setup.sh service
  bash setup.sh start
  bash setup.sh status
  bash setup.sh menu

Important environment variables:
  USE_REVERSE_PROXY=0|1
  PORT=8080
  FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.5
  ROOT_PATH=/cvd
  PUBLIC_BASE_URL=https://example.invalid/cvd/
  LOCALE_VALUE=de_DE.UTF-8
  TIMEZONE_VALUE=Europe/Berlin
  KEYBOARD_LAYOUT=de
  WIFI_COUNTRY=DE
  HOSTNAME_VALUE=cvd-tracker
  SSH_PASSWORD_LOGIN=0|1

Notes:
  - In interactive mode, the script asks whether HTTPS runs behind an external reverse proxy.
  - forwarded_allow_ips must contain trusted proxy IPs only, not browser IPs and not ports.
  - Without reverse proxy, the app listens on PORT and the script stores a direct http://... URL.
  - With reverse proxy, the app still listens on PORT and the firewall is restricted to FORWARDED_ALLOW_IPS.
EOF
}

show_menu() {
  detect_user

  PS3="Select operation: "
  local options=(
    "Full Setup"
    "Show Network"
    "Install System Packages"
    "Configure Pi OS"
    "Configure SSH"
    "Clone/Verify Repo"
    "Install micromamba"
    "Create/Update Environment"
    "Install pip Requirements"
    "Configure App"
    "Configure Firewall"
    "Install Service"
    "Start Services"
    "Service Status"
    "Quit"
  )

  echo "CVD-Tracker Raspberry Pi setup"
  select _ in "${options[@]}"; do
    case "$REPLY" in
      1) full_setup ;;
      2) show_network_inventory ;;
      3) install_system_packages ;;
      4) configure_pi_os ;;
      5) setup_ssh_server ;;
      6) ensure_repo_present; verify_repo ;;
      7) install_micromamba ;;
      8) create_mamba_env ;;
      9) create_mamba_env; install_python_requirements ;;
      10) prompt_for_access_settings; prepare_install_settings; create_mamba_env; configure_application ;;
      11) prompt_for_access_settings; prepare_install_settings; setup_firewall ;;
      12) setup_systemd_service ;;
      13) start_services ;;
      14) sudo systemctl status "${SERVICE_NAME}" ;;
      15) break ;;
      *) echo "Invalid selection" ;;
    esac
  done
}

main() {
  local command="${1:-menu}"

  case "$command" in
    full)
      full_setup
      ;;
    network)
      detect_user
      show_network_inventory
      ;;
    repo)
      detect_user
      ensure_supported_port
      ensure_repo_present
      verify_repo
      ;;
    system)
      detect_user
      install_system_packages
      ;;
    pi-config)
      detect_user
      configure_pi_os
      ;;
    ssh)
      detect_user
      setup_ssh_server
      ;;
    micromamba)
      detect_user
      install_micromamba
      ;;
    env)
      detect_user
      install_micromamba
      create_mamba_env
      ;;
    pip)
      detect_user
      install_micromamba
      create_mamba_env
      install_python_requirements
      ;;
    app-config)
      detect_user
      prompt_for_access_settings
      ensure_supported_port
      prepare_install_settings
      install_micromamba
      create_mamba_env
      configure_application
      ;;
    firewall)
      detect_user
      prompt_for_access_settings
      ensure_supported_port
      prepare_install_settings
      setup_firewall
      ;;
    service)
      detect_user
      setup_systemd_service
      ;;
    start)
      detect_user
      start_services
      ;;
    status)
      sudo systemctl status "${SERVICE_NAME}"
      ;;
    menu)
      show_menu
      ;;
    help|-h|--help)
      show_usage
      ;;
    *)
      die "Unknown command '${command}'. Use 'bash setup.sh help'."
      ;;
  esac
}

main "${1:-menu}"
