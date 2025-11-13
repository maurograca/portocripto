#!/usr/bin/env bash
set -euo pipefail

# COMANDOS PRINCIPAIS
# -------------------------------------
#
# Conceder permissão ao script
# chmod +x miner_piz.sh
#
# Instalar tudo e compilar (--threads opcional, padrão 2)
# ./miner_piz.sh setup --threads 2
#
# Inicializar banco de dados e sincronizar JSONs (--sync opcional)
# ./miner_piz.sh init ~/Downloads/jsons --sync
#
# Executar orquestrador com 3 solvers (--max-solvers opcional, padrão 2)
# ./miner_piz.sh run --max-solvers 3


# --------------------------- Config padrão ---------------------------
REPO_URL="https://github.com/mpizenberg/ce-ashmaize.git"
BRANCH="piz"
REPO_DIR="ce-ashmaize"
CLI_DIR="cli_hunt"
SOLVER_DIR="$CLI_DIR/rust_solver"
PY_DIR="$CLI_DIR/python_orchestrator"

# Caminho da pasta onde este script está (para salvar os auxiliares "ao lado do script")
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# --------------------------- UI helpers -----------------------------
c_reset="\033[0m"; c_green="\033[32m"; c_yellow="\033[33m"; c_red="\033[31m"; c_blue="\033[34m"
log()   { echo -e "${c_green}✔${c_reset} $*"; }
info()  { echo -e "${c_blue}»${c_reset} $*"; }
warn()  { echo -e "${c_yellow}⚠${c_reset} $*"; }
err()   { echo -e "${c_red}✖${c_reset} $*" >&2; }

# --------------------------- Detect OS -------------------------------
detect_os() {
  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt >/dev/null 2>&1; then echo "debian";
    elif command -v dnf >/dev/null 2>&1; then echo "fedora";
    elif command -v pacman >/dev/null 2>&1; then echo "arch";
    else echo "linux";
    fi
  elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "mac"
  else
    echo "unknown"
  fi
}

# --------------------------- Auto-install deps -----------------------
install_prereqs() {
  local os="$(detect_os)"
  info "Detectando sistema operacional: $os"

# curl + build-essential
  if ! command -v curl >/dev/null 2>&1 || ! dpkg -s build-essential >/dev/null 2>&1 2>/dev/null; then
    info "Instalando dependências básicas (curl e build-essential)..."
    case "$os" in
      debian)
        sudo apt update && sudo apt install -y curl build-essential
        ;;
      fedora)
        sudo dnf install -y curl @development-tools
        ;;
      arch)
        sudo pacman -Sy --noconfirm curl base-devel
        ;;
      mac)
        brew install curl
        xcode-select --install || true
        ;;
      *)
        err "Não sei instalar curl/build-essential neste sistema. Instale manualmente."
        exit 1
        ;;
    esac
  fi
  log "curl $(curl --version | head -n1) e ferramentas de build disponíveis."

  # Git
  if ! command -v git >/dev/null 2>&1; then
    info "Instalando git..."
    case "$os" in
      debian) sudo apt update && sudo apt install -y git ;;
      fedora) sudo dnf install -y git ;;
      arch) sudo pacman -Sy --noconfirm git ;;
      mac) brew install git ;;
      *) err "Não sei instalar git neste sistema. Instale manualmente."; exit 1 ;;
    esac
  fi

  # Python3 + pip
  if ! command -v python3 >/dev/null 2>&1; then
    info "Instalando Python 3..."
    case "$os" in
      debian) sudo apt update && sudo apt install -y python3 python3-pip ;;
      fedora) sudo dnf install -y python3 python3-pip ;;
      arch) sudo pacman -Sy --noconfirm python python-pip ;;
      mac) brew install python ;;
      *) err "Não sei instalar Python neste sistema. Instale manualmente."; exit 1 ;;
    esac
  fi
  log "Python $(python3 --version) disponível."

  # Criar alias 'python' -> 'python3' se não existir
  if ! command -v python >/dev/null 2>&1; then
    info "Criando alias 'python' → 'python3'"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$(command -v python3)" "$HOME/.local/bin/python"
    export PATH="$HOME/.local/bin:$PATH"
    if ! grep -qs 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null; then
      echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    fi
    if ! grep -qs 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.zshrc" 2>/dev/null; then
      echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
    fi
    hash -r
  fi
  log "Alias 'python' configurado → $(command -v python)"

  # Rust + Cargo
  if command -v cargo >/dev/null 2>&1 && command -v rustc >/dev/null 2>&1; then
    log "Rust já instalado: $(rustc --version)"
  else
    if command -v rustup >/dev/null 2>&1; then
      info "rustup encontrado; instalando toolchain estável…"
      rustup toolchain install stable --profile minimal -y || rustup toolchain install stable --profile minimal
      rustup default stable
    else
      info "Instalando Rust e Cargo via rustup (perfil mínimo, não-interativo)…"
      curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path
    fi
    [[ -f "$HOME/.cargo/env" ]] && . "$HOME/.cargo/env"
    hash -r
    command -v cargo >/dev/null 2>&1 && command -v rustc >/dev/null 2>&1 || {
      err "Rust não ficou no PATH. Rode: source \"\$HOME/.cargo/env\" e tente novamente."; exit 1; }
    log "Rust pronto: $(rustc --version)"
  fi

# uv (instalador rápido de pacotes Python)
  if ! command -v uv >/dev/null 2>&1; then
    info "Instalando uv..."
    case "$os" in
      debian|fedora|arch)
        curl -LsSf https://astral.sh/uv/install.sh | sh
        ;;
      mac)
        brew install uv
        ;;
      *)
        err "Não sei instalar uv neste sistema. Instale manualmente a partir de https://astral.sh/uv/"
        ;;
    esac
    export PATH="$HOME/.local/bin:$PATH"
  fi
  log "uv $(uv --version) disponível."

  log "Dependências instaladas com sucesso!"
}

# --------------------------- Git clone/atualiza ---------------------
clone_or_update_repo() {
  local force="$1"
  if [[ "$force" == "1" && -d "$REPO_DIR" ]]; then
    info "Removendo diretório existente (forçado)"
    rm -rf "$REPO_DIR"
  fi

  if [[ -d "$REPO_DIR/.git" ]]; then
    info "Atualizando repositório existente..."
    git -C "$REPO_DIR" fetch --all --prune
    git -C "$REPO_DIR" checkout "$BRANCH"
    git -C "$REPO_DIR" pull --ff-only origin "$BRANCH"
  else
    info "Clonando repositório..."
    git clone "$REPO_URL"
    git -C "$REPO_DIR" checkout "$BRANCH"
  fi
  log "Repositório pronto!"
}

# --------------------------- Ajusta threads -------------------------
maybe_patch_threads() {
  local threads="${1:-}"
  [[ -z "$threads" ]] && return 0
  local main_rs="$REPO_DIR/$SOLVER_DIR/src/main.rs"
  if [[ -f "$main_rs" ]]; then
    info "Configurando NUM_THREADS=$threads"
    sed -E -i'' "s/(const[[:space:]]+NUM_THREADS:[[:space:]]*u64[[:space:]]*=[[:space:]]*)[0-9]+;/\1${threads};/" "$main_rs" || true
  else
    warn "Arquivo main.rs não encontrado para ajustar threads."
  fi
}

# --------------------------- Build solver ---------------------------
build_solver() {
  info "Compilando solver em modo release..."
  (cd "$REPO_DIR/$SOLVER_DIR" && cargo build --release)
  log "Solver compilado com sucesso!"
}

# --------------------------- Setup Python ---------------------------
setup_python() {
  info "Instalando dependências Python com uv..."
  (cd "$REPO_DIR/$PY_DIR" && uv sync)
  log "Ambiente Python configurado!"
}

# --------------------------- Init DB (com sync opcional) -------------
init_db() {
  local json_dir="$1"
  local do_sync="${2:-0}"                 # 1 para sincronizar; 0 para não
  local sync_source="${3:-challenges_from_the_last_24_hours.json}"

  local target_web="$REPO_DIR/$PY_DIR/web"
  mkdir -p "$target_web"

  # Copiar JSONs exportados
  info "Copiando JSONs de $json_dir para $target_web"
  if compgen -G "$json_dir"/*.json >/dev/null; then
    cp "$json_dir"/*.json "$target_web"/
  else
    err "Nenhum .json encontrado em: $json_dir"
    exit 1
  fi

  # (Opcional) Sincronização antes do main.py init
  if [[ "$do_sync" == "1" ]]; then
    info "Sincronização de JSONs habilitada (--sync). Baixando/atualizando utilitários…"
    ( cd "$SCRIPT_DIR"
      curl -LsSf "https://gist.githubusercontent.com/portocripto/ac43c57ba4256cccd68ebd7b12a4517d/raw/sync_challenges.py" -o "sync_challenges.py"
      curl -LsSf "https://gist.githubusercontent.com/portocripto/2779db1e5213e190cca447160c2c295b/raw/challenges_from_the_last_24_hours.json" -o "challenges_from_the_last_24_hours.json"
    )
    log "Auxiliares prontos em: $SCRIPT_DIR"

    # Executar a sincronização (fonte default: challenges_from_the_last_24_hours.json)
    info "Executando sync_challenges.py (source: $sync_source)…"
    ( cd "$SCRIPT_DIR"
      python3 sync_challenges.py --no-backup --source "$sync_source" --targets "$target_web"/*scavenger-mine-export-*.json
    )
    log "Sincronização concluída."
  else
    info "Sincronização de JSONs DESABILITADA (use --sync para habilitar)."
  fi

  # Inicializar o banco de dados
  info "Inicializando banco de dados (challenges.json)…"
  (cd "$REPO_DIR/$PY_DIR" && uv run main.py init web/*.json)
  log "Banco de dados inicializado com sucesso!"
}

# --------------------------- Run orchestrator -----------------------
run_orchestrator() {
  local max_solvers="${1:-}"
  info "Iniciando o orquestrador…"
  if [[ -n "$max_solvers" ]]; then
    (cd "$REPO_DIR/$PY_DIR" && uv run main.py run --max-solvers "$max_solvers" --challenge-selection first)
  else
    (cd "$REPO_DIR/$PY_DIR" && uv run main.py run --challenge-selection first)
  fi
}

# --------------------------- CLI ------------------------------------
usage() {
  cat <<EOF
Uso:
  ./miner_piz.sh setup [--force] [--threads N]
      Checa/instala dependências (git, python3+pip, alias python, rust/cargo, uv),
      clona/atualiza repo (branch 'piz'), ajusta threads (opcional) e compila o solver.

  ./miner_piz.sh init <dir_jsons> [--sync] [--sync-source <arquivo>]
      Copia os JSONs exportados para python_orchestrator/web/.
      Se --sync for usado, baixa/atualiza 'sync_challenges.py' e
      'challenges_from_the_last_24_hours.json' ao lado do script e executa:
        python sync_challenges.py --no-backup --source <arquivo> --targets <web/*.json>
      (padrão de <arquivo>: challenges_from_the_last_24_hours.json)

  ./miner_piz.sh run [--max-solvers N]
      Inicia o TUI do orquestrador.

Opções gerais:
  --force             Força re-clone do repositório durante 'setup'
  --threads N         Ajusta a constante NUM_THREADS do solver antes do build
  --sync              Habilita sincronização de JSONs no 'init'
  --sync-source FILE  Arquivo fonte para a sincronização (default: challenges_from_the_last_24_hours.json)
  --max-solvers N     Número máximo de solvers em paralelo no 'run'

Exemplos:
  ./miner_piz.sh setup --threads 4
  ./miner_piz.sh init ~/Downloads/jsons --sync
  ./miner_piz.sh init ~/Downloads/jsons --sync --sync-source challenge.json
  ./miner_piz.sh run --max-solvers 3
EOF
}

ACTION=""
FORCE="0"
THREADS=""
MAX_SOLVERS=""
JSON_DIR=""
SYNC="0"
SYNC_SOURCE=""

# Parse de argumentos
while [[ $# -gt 0 ]]; do
  case "$1" in
    setup|init|run) ACTION="$1"; shift ;;
    --force) FORCE="1"; shift ;;
    --threads) THREADS="${2:-}"; shift 2 ;;
    --max-solvers) MAX_SOLVERS="${2:-}"; shift 2 ;;
    --sync) SYNC="1"; shift ;;
    --sync-source) SYNC_SOURCE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ -z "$JSON_DIR" && "$ACTION" == "init" ]]; then
        JSON_DIR="$1"; shift
      else
        err "Argumento desconhecido: $1"; usage; exit 1
      fi
      ;;
  esac
done

# Dispatcher
case "$ACTION" in
  setup)
    install_prereqs
    clone_or_update_repo "$FORCE"
    [[ -n "$THREADS" ]] && maybe_patch_threads "$THREADS"
    build_solver
    setup_python
    log "✅ Setup completo!"
    ;;
  init)
    [[ -z "$JSON_DIR" ]] && { err "Informe o diretório com JSONs exportados."; usage; exit 1; }
    if [[ -n "$SYNC_SOURCE" ]]; then
      init_db "$JSON_DIR" "$SYNC" "$SYNC_SOURCE"
    else
      init_db "$JSON_DIR" "$SYNC"
    fi
    ;;
  run)
    run_orchestrator "$MAX_SOLVERS"
    ;;
  *)
    usage; exit 1 ;;
esac
