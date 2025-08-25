#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Konfiguration (kann via ENV überschrieben werden)
# -----------------------------------------------------------------------------
ENV_NAME="${ENV_NAME:-cvd-tracker}"
CONDA_CHANNEL="${CONDA_CHANNEL:-conda-forge}"
CLONE_URL="${CLONE_URL:-https://github.com/5random/neu.git}"
CLONE_DIR_DEFAULT="${HOME}/cvd_tracker"          # wird nach detect_user ggf. auf USER_HOME angepasst
CLONE_DIR="${CLONE_DIR:-$CLONE_DIR_DEFAULT}"
PORT="${PORT:-8080}"                              # App nutzt aktuell fest 8080 in main.py
SERVICE_NAME="${SERVICE_NAME:-cvd_tracker}"

# micromamba Binärpfad – wird nach detect_user auf USER_HOME angepasst, falls nicht explizit gesetzt
MICROMAMBA_BIN_DEFAULT="${HOME}/.local/bin/micromamba"
MICROMAMBA_BIN="${MICROMAMBA_BIN:-$MICROMAMBA_BIN_DEFAULT}"

# Root Prefix der micromamba-Installation/Envs – nach detect_user gesetzt
MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-}"

# Wird dynamisch gesucht (enviroment.yaml / environment.yml / environment.yaml)
ENV_FILE=""

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
msg() { echo -e "\n[setup] $*"; }

detect_user() {
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    RUN_USER="$SUDO_USER"
  else
    RUN_USER="$USER"
  fi
  # robustes HOME des Zielusers
  if USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"; then
    : # ok
  else
    USER_HOME="$HOME"
  fi

  # Defaults an RUN_USER anpassen, wenn nicht explizit gesetzt
  if [[ "${CLONE_DIR}" == "${CLONE_DIR_DEFAULT}" ]]; then
    CLONE_DIR="${USER_HOME}/cvd_tracker"
  fi

  # Binärpfad und Root Prefix auf RUN_USER ausrichten, falls nicht explizit gesetzt
  if [[ "${MICROMAMBA_BIN}" == "${MICROMAMBA_BIN_DEFAULT}" ]]; then
    MICROMAMBA_BIN="${USER_HOME}/.local/bin/micromamba"
  fi
  if [[ -z "${MAMBA_ROOT_PREFIX}" ]]; then
    MAMBA_ROOT_PREFIX="${USER_HOME}/micromamba"
  fi

  export MAMBA_ROOT_PREFIX
  msg "RUN_USER=${RUN_USER}"
  msg "USER_HOME=${USER_HOME}"
  msg "MICROMAMBA_BIN=${MICROMAMBA_BIN}"
  msg "MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX}"
  msg "CLONE_DIR=${CLONE_DIR}"
}

require_64bit_arm() {
  local arch
  arch="$(uname -m)"
  case "$arch" in
    aarch64|arm64) msg "64-bit ARM erkannt: $arch" ;;
    *)
      msg "WARNUNG: Architektur '$arch' erkannt. Für micromamba + conda-forge OpenCV wird üblicherweise 64-bit (aarch64) benötigt."
      msg "Auf 32-bit Raspberry Pi OS (armv7l) schlägt die conda OpenCV-Installation i.d.R. fehl. Bitte 64-bit OS nutzen."
      ;;
  esac
}

show_mac_addresses() {
  msg "Netzwerk-MAC-Adressen:"
  # Liste aller Interfaces außer lo, inklusive Status
  ip -o link show | awk -F': ' '$2 != "lo" {print $2}' | while read -r ifc; do
    mac="$(cat "/sys/class/net/$ifc/address" 2>/dev/null || true)"
    state="$(cat "/sys/class/net/$ifc/operstate" 2>/dev/null || true)"
    echo "  ${ifc}  MAC: ${mac}  STATE: ${state}"
  done
}

install_system_packages() {
  msg "Installiere Systempakete (apt)..."
  sudo apt update
  sudo apt install -y --no-install-recommends \
    ufw git wget curl v4l-utils \
    libgl1 libglib2.0-0 ca-certificates \
    openssh-server
}

install_micromamba() {
  if command -v micromamba >/dev/null 2>&1; then
    MICROMAMBA_BIN="$(command -v micromamba)"
    msg "micromamba bereits im PATH gefunden: $MICROMAMBA_BIN"
    return
  fi
  if [[ -x "$MICROMAMBA_BIN" ]]; then
    msg "micromamba bereits installiert: $MICROMAMBA_BIN"
    return
  fi

  msg "Installiere micromamba für Benutzer '${RUN_USER}'..."
  # Installationsskript als Zielbenutzer ausführen, damit unter USER_HOME installiert wird
  sudo -u "$RUN_USER" bash -lc "curl -Ls https://micro.mamba.pm/install.sh | bash"
  # Nach Installation sollte der Binärpfad existieren
  if [[ ! -x "$MICROMAMBA_BIN" ]]; then
    echo "micromamba nicht gefunden unter $MICROMAMBA_BIN" >&2
    exit 1
  fi
}

find_env_file() {
  # Bevorzugte Reihenfolge, inkl. Schreibweise 'enviroment.yaml' (wie im Repo)
  local candidates=(
    "${CLONE_DIR}/enviroment.yaml"
    "${CLONE_DIR}/environment.yml"
    "${CLONE_DIR}/environment.yaml"
  )
  ENV_FILE=""
  for c in "${candidates[@]}"; do
    if [[ -f "$c" ]]; then
      ENV_FILE="$c"
      break
    fi
  done
  if [[ -n "$ENV_FILE" ]]; then
    msg "Gefundene Umgebungsdatei: $ENV_FILE"
  else
    msg "Keine Umgebungsdatei gefunden. Fallback auf explizite Paketliste."
  fi
}

create_mamba_env() {
  msg "Erstelle/aktualisiere mamba-Umgebung: $ENV_NAME"
  find_env_file

  if [[ -n "$ENV_FILE" ]]; then
    if "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" env list | grep -qE "^[[:space:]]*$ENV_NAME[[:space:]]"; then
      msg "Umgebung '$ENV_NAME' existiert. Aktualisiere via $ENV_FILE ..."
      "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" env update -n "$ENV_NAME" -f "$ENV_FILE"
    else
      msg "Erzeuge Umgebung '$ENV_NAME' via $ENV_FILE ..."
      "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" env create -n "$ENV_NAME" -f "$ENV_FILE"
    fi
  else
    # Fallback ohne Umgebungsdatei
    if "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" env list | grep -qE "^[[:space:]]*$ENV_NAME[[:space:]]"; then
      msg "Umgebung '$ENV_NAME' existiert bereits. Überspringe Erstellung."
    else
      "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" create -y -n "$ENV_NAME" -c "$CONDA_CHANNEL" \
        python=3.11 \
        opencv numpy pillow pyyaml python-dateutil \
        fastapi uvicorn requests pytest pip wheel setuptools
    fi
  fi
}

clone_or_update_repo() {
  msg "Klon/Update Repository in $CLONE_DIR"
  if [[ -d "$CLONE_DIR/.git" ]]; then
    sudo -u "$RUN_USER" git -C "$CLONE_DIR" fetch --all --prune
    sudo -u "$RUN_USER" git -C "$CLONE_DIR" pull --ff-only
  else
    sudo -u "$RUN_USER" mkdir -p "$(dirname "$CLONE_DIR")"
    sudo -u "$RUN_USER" git clone "$CLONE_URL" "$CLONE_DIR"
  fi
}

install_python_requirements() {
  msg "Installiere Python-Abhängigkeiten via pip in der mamba-Umgebung"
  if [[ ! -f "$CLONE_DIR/requirements.txt" ]]; then
    echo "requirements.txt nicht gefunden unter $CLONE_DIR/requirements.txt" >&2
    exit 1
  fi

  "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" run -n "$ENV_NAME" python -m pip install -U pip setuptools wheel

  # Vermeide Konflikte: opencv kommt aus conda; filtere Duplikate aus requirements.txt
  local FILTER_PKGS="opencv-python|numpy|pillow|pyyaml|python-dateutil|fastapi|uvicorn|requests|pytest"
  if [[ -n "${ENV_FILE:-}" ]] && grep -qiE '(^|\s|-)nicegui' "$ENV_FILE"; then
    FILTER_PKGS="${FILTER_PKGS}|nicegui"
  fi

  local TMP_REQ
  TMP_REQ="$(mktemp)"
  sed -E '/^\s*($|#)/d' "$CLONE_DIR/requirements.txt" \
    | grep -Eiv "^\s*(${FILTER_PKGS})(\s*[<>=!]=.*)?\s*$" \
    > "$TMP_REQ"

  if [[ -s "$TMP_REQ" ]]; then
    msg "Zusätzliche pip-Pakete werden installiert:"
    cat "$TMP_REQ"
    "$MICROMAMBA_BIN" --root-prefix "$MAMBA_ROOT_PREFIX" run -n "$ENV_NAME" python -m pip install -r "$TMP_REQ"
  else
    msg "Keine zusätzlichen pip-Pakete erforderlich (durch conda/env abgedeckt)."
  fi
  rm -f "$TMP_REQ"
}

setup_firewall() {
  msg "Konfiguriere UFW (SSH und Port $PORT/tcp erlauben)..."
  # SSH zuerst freischalten, um Lockout zu vermeiden
  sudo ufw allow OpenSSH || sudo ufw allow 22/tcp || true
  sudo ufw limit ssh || true
  sudo ufw allow "$PORT"/tcp || true
  sudo ufw --force enable || true
  sudo ufw status verbose || true
}

setup_ssh_password_login() {
  msg "Konfiguriere SSH-Server (Passwort-Login aktivieren)..."
  local FILE="/etc/ssh/sshd_config"

  # Paket und Dienst sicherstellen
  sudo apt update
  sudo apt install -y --no-install-recommends openssh-server
  sudo systemctl enable ssh

  # Backup einmalig anlegen
  if [[ -f "$FILE" && ! -f "$FILE.bak" ]]; then
    sudo cp "$FILE" "$FILE.bak"
  fi

  # Sichere Defaults setzen (idempotent)
  # - Passwort-Login erlauben
  sudo sed -ri 's/^\s*#?\s*PasswordAuthentication\s+.*/PasswordAuthentication yes/' "$FILE" || true
  grep -Eq '^\s*PasswordAuthentication\s+yes' "$FILE" || echo 'PasswordAuthentication yes' | sudo tee -a "$FILE" >/dev/null

  # - Root-Login verbieten
  sudo sed -ri 's/^\s*#?\s*PermitRootLogin\s+.*/PermitRootLogin no/' "$FILE" || true
  grep -Eq '^\s*PermitRootLogin\s+no' "$FILE" || echo 'PermitRootLogin no' | sudo tee -a "$FILE" >/dev/null

  # - PAM benutzen und leere Passwörter verbieten
  sudo sed -ri 's/^\s*#?\s*UsePAM\s+.*/UsePAM yes/' "$FILE" || true
  grep -Eq '^\s*UsePAM\s+yes' "$FILE" || echo 'UsePAM yes' | sudo tee -a "$FILE" >/dev/null

  sudo sed -ri 's/^\s*#?\s*PermitEmptyPasswords\s+.*/PermitEmptyPasswords no/' "$FILE" || true
  grep -Eq '^\s*PermitEmptyPasswords\s+no' "$FILE" || echo 'PermitEmptyPasswords no' | sudo tee -a "$FILE" >/dev/null

  # Optional: X11 aus
  sudo sed -ri 's/^\s*#?\s*X11Forwarding\s+.*/X11Forwarding no/' "$FILE" || true
  grep -Eq '^\s*X11Forwarding\s+no' "$FILE" || echo 'X11Forwarding no' | sudo tee -a "$FILE" >/dev/null

  sudo systemctl restart ssh || true

  msg "Hinweis: Setze bei Bedarf ein Passwort für ${RUN_USER} mit: sudo passwd ${RUN_USER}"
}

ensure_user_video_group() {
  msg "Füge Benutzer '$RUN_USER' zur Gruppe 'video' hinzu (Kamera-Zugriff)..."
  sudo usermod -aG video "$RUN_USER" || true
}

setup_systemd_service() {
  msg "Erzeuge systemd Service: $SERVICE_NAME"
  local SVC="/etc/systemd/system/${SERVICE_NAME}.service"

  sudo tee "$SVC" >/dev/null <<EOF
[Unit]
Description=CVD-Tracker (NiceGUI) Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${CLONE_DIR}
Environment=MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX}
Environment=PYTHONUNBUFFERED=1
ExecStart=${MICROMAMBA_BIN} --root-prefix ${MAMBA_ROOT_PREFIX} run -n ${ENV_NAME} python ${CLONE_DIR}/main.py --config ${CLONE_DIR}/config/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}.service"
}

print_hints() {
  echo -e "\nFertig. Nützliche Befehle:"
  echo "  MACs anzeigen: ./setup.sh (Menü) -> MAC-Adressen anzeigen"
  echo "  sudo systemctl start ${SERVICE_NAME}"
  echo "  sudo systemctl status ${SERVICE_NAME}"
  echo "  sudo journalctl -u ${SERVICE_NAME} -f"
  echo "  ${MICROMAMBA_BIN} --root-prefix ${MAMBA_ROOT_PREFIX} run -n ${ENV_NAME} python ${CLONE_DIR}/main.py --config ${CLONE_DIR}/config/config.yaml"
  if [[ -n "${ENV_FILE:-}" ]]; then
    echo "Umgebungsdatei verwendet: ${ENV_FILE}"
  fi
  echo "Service lauscht auf Port ${PORT} (GUI: http://<IP>:${PORT})"
  echo "SSH Passwort setzen (falls nötig): sudo passwd ${RUN_USER}"
}

full_setup() {
  detect_user
  require_64bit_arm
  show_mac_addresses
  install_system_packages
  setup_ssh_password_login
  install_micromamba
  clone_or_update_repo
  create_mamba_env
  install_python_requirements
  ensure_user_video_group
  setup_firewall
  setup_systemd_service
  print_hints
}

# Interaktives Menü
PS3="Operation wählen: "
OPTIONS=(
  "Full Setup"
  "MAC-Adressen anzeigen"
  "Systempakete installieren"
  "SSH konfigurieren (Passwortlogin)"
  "micromamba installieren"
  "mamba-Umgebung erstellen/aktualisieren"
  "Repository klonen/aktualisieren"
  "Python-Requirements in Env installieren"
  "Firewall konfigurieren"
  "systemd-Service einrichten"
  "Service starten"
  "Service-Status"
  "Beenden"
)

echo "CVD-Tracker Setup (mamba/micromamba)"
select opt in "${OPTIONS[@]}"; do
  case "$REPLY" in
    1) full_setup ;;
    2) show_mac_addresses ;;
    3) install_system_packages ;;
    4) setup_ssh_password_login ;;
    5) install_micromamba ;;
    6) create_mamba_env ;;
    7) clone_or_update_repo ;;
    8) install_python_requirements ;;
    9) setup_firewall ;;
    10) setup_systemd_service ;;
    11) sudo systemctl start "${SERVICE_NAME}" ;;
    12) sudo systemctl status "${SERVICE_NAME}" ;;
    13) break ;;
    *) echo "Ungültige Auswahl";;
  esac
done