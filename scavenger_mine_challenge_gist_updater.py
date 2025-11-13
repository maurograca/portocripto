#!/usr/bin/env python3
"""
scavenger_mine_challenge_gist_updater.py

- Busca novo challenge a cada 10 minutos em https://sm.midnight.gd/api/challenge usando curl_cffi
  (impersonação de browser chrome110).
- Lê o arquivo atual do Gist, que contém:
    {
      "challenge_queue": [ {...}, {...}, ... ]
    }

- Converte o item da API (challenge.json) para o formato esperado em cada item
  de challenge_queue:

    challengeId        <- challenge_id
    challengeNumber    <- (day - 1) * 24 + challenge_number
    challengeTotal     <- total_challenges        (campo na RAIZ do JSON)
    campaignDay        <- day
    difficulty         <- difficulty
    status             <- "available"            (fixo)
    noPreMine          <- no_pre_mine
    noPreMineHour      <- no_pre_mine_hour
    latestSubmission   <- latest_submission
    availableAt        <- issued_at

- Atualiza challenge_queue (Gist):
    - insere o novo desafio;
    - ordena por challengeId (string) do MAIOR pro MENOR;
    - remove duplicados por challengeId;
    - mantém no máximo 24 itens (cortando do fim).

- Critério de “novo desafio” baseado em challengeNumber:
    - Usa o challengeNumber do primeiro item já armazenado no Gist (topo da challenge_queue).
    - Se challengeNumber (API) <= challengeNumber (Gist) → NÃO atualiza o Gist.
    - Se challengeNumber (API) > challengeNumber (Gist) → atualiza o Gist (inserindo o novo item
      e regravando o arquivo do Gist).

- Atualiza a descrição do Gist, preservando o texto:
    "Desafios das Últimas 24h: 262-286 (Scavenger Mine - Airdrop Midnight)"
  e substituindo apenas o intervalo numérico com base em challengeNumber.

- Faz PATCH no GitHub Gist usando GITHUB_TOKEN e GIST_ID do ambiente.

Variavéis de ambiente necessárias:
- GITHUB_TOKEN: GitHub token classic (GitHub > Settings > Developer settings > Personal access token).
- GIST_ID: ID do Gist contendo o challenges_from_the_last_24_hours.json.

Para testes locais:
- TEST_CHALLENGE_PATH: caminho de challenge.json salvo localmente.
- LOCAL_GIST_FILE: caminho de challenges_from_the_last_24_hours local
  (não toca no Gist real).
"""

import os
import json
import time
import traceback
import datetime
import time

# ==========================
#   CONFIG / CONSTANTES
# ==========================

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")

# Nome do arquivo dentro do Gist (ajuste se for .txt, .json, etc)
GIST_FILENAME = os.getenv(
    "GIST_FILENAME",
    "challenges_from_the_last_24_hours.json",
)

API_URL = "https://sm.midnight.gd/api/challenge"
GITHUB_GIST_URL = "https://api.github.com/gists"

# Arquivos locais para testes (sem tocar no Gist real)
TEST_CHALLENGE_PATH = os.getenv("TEST_CHALLENGE_PATH")      # ex: /mnt/data/challenge.json
LOCAL_GIST_FILE = os.getenv("LOCAL_GIST_FILE")              # ex: /mnt/data/challenges_from_the_last_24_hours.txt

# Descrição padrão base (será sobrescrita pelo valor atual do Gist se existir)
DEFAULT_DESC = "Desafios das Últimas 24h: 262-286 (Scavenger Mine - Airdrop Midnight)"

# User-Agent and headers to "impersonate chrome110"
CHROME110_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
)
COMMON_HEADERS = {
    "User-Agent": CHROME110_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# curl_cffi para API Midnight
try:
    from curl_cffi import requests as cf_requests # type: ignore
except Exception:
    print("Este script exige 'curl_cffi'. Instale com: pip install curl_cffi")
    raise

# requests normal para GitHub
import requests as gh_requests


# ==========================
#   FUNÇÕES AUXILIARES
# ==========================

def fetch_challenge_payload():
    """
    Busca o JSON bruto da API Midnight.

    Formato esperado (API):
        {
          "code": "...",
          "challenge": { ... },
          "mining_period_ends": "...",
          "max_day": ...,
          "total_challenges": ...,
          "current_day": ...,
          "next_challenge_starts_at": "..."
        }

    - Se TEST_CHALLENGE_PATH estiver definido e existir, usa esse arquivo local.
    - Caso contrário, faz GET em https://sm.midnight.gd/api/challenge.
    """
    if TEST_CHALLENGE_PATH and os.path.exists(TEST_CHALLENGE_PATH):
        print(f"[info] Lendo challenge de arquivo local: {TEST_CHALLENGE_PATH}")
        with open(TEST_CHALLENGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    print(f"[info] Buscando desafio na API: {API_URL}")
    # curl_cffi Session com impersonate de browser
    sess = cf_requests.Session(impersonate="chrome110")
    sess.headers.update(COMMON_HEADERS)
    resp = sess.get(API_URL, timeout=20)
    resp.raise_for_status()
    return resp.json()


def compute_global_challenge_number(day, challenge_number):
    """
    Calcula challengeNumber global a partir de:
        - dia da campanha (day)
        - challenge_number daquele dia

    Fórmula:
        challengeNumber = (day - 1) * 24 + challenge_number

    Exemplo:
        day = 15, challenge_number = 4
        -> (15 - 1) * 24 + 4 = 340
    """
    if not isinstance(day, int) or not isinstance(challenge_number, int):
        return None
    return (day - 1) * 24 + challenge_number


def map_api_to_gist_item(payload: dict) -> dict:
    """
    Extrai o item de challenge do payload da API e converte para o formato
    usado na challenge_queue do Gist.

    Assumimos o formato oficial da API:
        {
          "challenge": { ... },
          "total_challenges": <int>,
          ...
        }

    Mapeamento:

    challengeId        <- challenge_id
    challengeNumber    <- (day - 1) * 24 + challenge_number
    challengeTotal     <- total_challenges
    campaignDay        <- day
    difficulty         <- difficulty
    status             <- "available"
    noPreMine          <- no_pre_mine
    noPreMineHour      <- no_pre_mine_hour
    latestSubmission   <- latest_submission
    availableAt        <- issued_at
    """
    if not isinstance(payload, dict):
        raise RuntimeError("Payload da API não é um dict no formato esperado.")

    challenge = payload.get("challenge")
    if not isinstance(challenge, dict):
        raise RuntimeError("Campo 'challenge' não encontrado ou inválido no payload da API.")

    total_challenges = payload.get("total_challenges")

    day = challenge.get("day")
    challenge_number = challenge.get("challenge_number")

    global_challenge_number = compute_global_challenge_number(day, challenge_number)

    # fallback: se não der pra calcular, usa challenge_number cru
    if global_challenge_number is None:
        global_challenge_number = challenge_number

    return {
        "challengeId":      challenge.get("challenge_id"),
        "challengeNumber":  global_challenge_number,
        "challengeTotal":   total_challenges,
        "campaignDay":      day,
        "difficulty":       challenge.get("difficulty"),
        "status":           "available",
        "noPreMine":        challenge.get("no_pre_mine"),
        "noPreMineHour":    challenge.get("no_pre_mine_hour"),
        "latestSubmission": challenge.get("latest_submission"),
        "availableAt":      challenge.get("issued_at"),
    }


def get_gist_content():
    """
    Lê o conteúdo atual do Gist via API do GitHub.

    Retorna:
        (file_name, content_str, description)
    """
    assert GITHUB_TOKEN and GIST_ID, "GITHUB_TOKEN e GIST_ID precisam estar definidos."

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "update-gist-challenges-script",
    }
    url = f"{GITHUB_GIST_URL}/{GIST_ID}"

    resp = gh_requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    gist = resp.json()

    description = gist.get("description", "")
    files = gist.get("files", {})

    if GIST_FILENAME in files:
        fdata = files[GIST_FILENAME]
        content = fdata.get("content", "")
        name = GIST_FILENAME
    else:
        if not files:
            raise RuntimeError("Gist não contém arquivos.")
        # pega o primeiro arquivo se o nome não bater
        name, fdata = next(iter(files.items()))
        content = fdata.get("content", "")

    return name, content, description


def parse_challenge_queue(content_str: str):
    """
    Interpreta o conteúdo do arquivo do Gist.

    Formatos aceitos:

    1) Objeto com challenge_queue:
        {
          "challenge_queue": [ {...}, {...}, ... ]
        }

    2) Lista direta:
        [ {...}, {...}, ... ]

    Retorna:
        lista de itens (challenge_queue).
    """
    content_str = content_str.strip()
    if not content_str:
        return []

    try:
        data = json.loads(content_str)
    except Exception:
        # fallback: arquivo inválido → começa vazio
        return []

    if isinstance(data, dict) and "challenge_queue" in data:
        q = data["challenge_queue"]
        return q if isinstance(q, list) else list(q)

    if isinstance(data, list):
        return data

    # fallback
    return []


def normalize_items(items):
    """
    Garante que todos os itens na challenge_queue sejam dict.

    Se houver strings JSON, tenta parse, senão embrulha em {"raw": ...}.
    """
    norm = []
    for it in items:
        if isinstance(it, dict):
            norm.append(it)
            continue
        if isinstance(it, str):
            try:
                j = json.loads(it)
                if isinstance(j, dict):
                    norm.append(j)
                    continue
            except Exception:
                pass
        norm.append({"raw": it})
    return norm


def sort_and_dedupe_by_challenge_id_desc(items):
    """
    Remove duplicados de challengeId e ordena do MAIOR pro MENOR.

    - challengeId é usado como chave de ordenação principal (string).
    - Itens sem challengeId vão para o fim.
    """
    seen = set()
    deduped = []

    for it in items:
        cid = it.get("challengeId")
        if cid is not None:
            if cid in seen:
                continue
            seen.add(cid)
        deduped.append(it)

    def key_fn(x):
        cid = x.get("challengeId")
        if isinstance(cid, str):
            return (0, cid)  # 0 = tem id
        return (1, "")       # 1 = sem id, vai pro fim

    deduped.sort(key=key_fn, reverse=True)
    return deduped


def keep_at_most_24(items):
    """
    Mantém no máximo 24 itens, cortando do fim se necessário.
    """
    if len(items) <= 24:
        return items
    return items[:24]


def update_description_range(description: str, items) -> str:
    """
    Gera SEMPRE a descrição no formato padrão, usando o menor e o maior
    challengeNumber da fila.

    Formato final:
        "Desafios das Últimas 24h: X-Y (Scavenger Mine - Airdrop Midnight)"
    """
    nums = [
        it.get("challengeNumber")
        for it in items
        if isinstance(it, dict) and isinstance(it.get("challengeNumber"), int)
    ]

    if not nums:
        # Se por algum motivo não tiver números, mantém a descrição antiga
        # ou cai no DEFAULT_DESC.
        return description or DEFAULT_DESC

    lo, hi = min(nums), max(nums)
    new_range = f"{lo}-{hi}"

    # Sempre gera uma nova descrição padronizada.
    return f"Desafios das Últimas 24h: {new_range} (Scavenger Mine - Airdrop Midnight)"


def patch_gist(file_name: str, new_content_str: str, new_description: str):
    """
    Faz PATCH no Gist para atualizar o arquivo e a descrição.
    """
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "update-gist-challenges-script",
    }
    url = f"{GITHUB_GIST_URL}/{GIST_ID}"

    payload = {
        "description": new_description,
        "files": {
            file_name: {
                "content": new_content_str
            }
        }
    }

    resp = gh_requests.patch(url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()


# ==========================
#   MAIN
# ==========================

def main():
    try:
        print("[info] --------------------------------------------------")
        print("[info] Iniciando scavenger_mine_challenge_gist_updater.py")
        print("[info] --------------------------------------------------")

        # 1) Busca payload da API (ou arquivo local de teste)
        payload = fetch_challenge_payload()

        # 2) Extrai o challenge e mapeia para o formato do Gist
        new_item = map_api_to_gist_item(payload)
        print("[info] Desafio via API (mapeado):", json.dumps(new_item, ensure_ascii=False))

        new_challenge_number = new_item.get("challengeNumber")

        # 3) Lê conteúdo atual (Gist real ou arquivo local de teste)
        if LOCAL_GIST_FILE and os.path.exists(LOCAL_GIST_FILE):
            print(f"[info] Usando arquivo local de Gist: {LOCAL_GIST_FILE}")
            with open(LOCAL_GIST_FILE, "r", encoding="utf-8") as f:
                content_str = f.read()
            file_name = GIST_FILENAME
            description = DEFAULT_DESC
        else:
            file_name, content_str, description = get_gist_content()
            print(f"[info] Lido Gist file={file_name}, desc='{description}'")

        # 4) Interpreta a challenge_queue
        queue = parse_challenge_queue(content_str)
        queue = normalize_items(queue)

        # 4.1) Obtém o último challengeNumber já armazenado no Gist
        last_challenge_number = None

        if queue and isinstance(queue[0], dict):
            first_cn = queue[0].get("challengeNumber")
            if isinstance(first_cn, int):
                last_challenge_number = first_cn

        # Fallback: se o primeiro item não tiver challengeNumber, usa o maior da fila
        if last_challenge_number is None:
            nums = [
                it.get("challengeNumber")
                for it in queue
                if isinstance(it, dict) and isinstance(it.get("challengeNumber"), int)
            ]
            if nums:
                last_challenge_number = max(nums)

        if last_challenge_number is not None:
            print(f"[info] Desafio via Gist: {last_challenge_number}")
        else:
            print("[info] Nenhum challengeNumber encontrado no Gist (fila vazia ou sem dados).")

        # Verifica diferença de challengeNumber e se existe desafio novo
        if isinstance(last_challenge_number, int) and isinstance(new_challenge_number, int):
            delta = new_challenge_number - last_challenge_number

            # Alerta forte se a diferença for maior que 1
            if delta > 1:
                print(
                    f"[ALERT] Diferença de challengeNumber maior que 1 "
                    f"entre API e Gist: API={new_challenge_number}, "
                    f"Gist={last_challenge_number}, delta={delta}"
                )

            # Se delta <= 0, não há desafio novo
            if delta <= 0:
                print(
                    f"[info] Nenhum desafio novo! API ({new_challenge_number}) "
                    f"<= Gist ({last_challenge_number}). Nada a fazer."
                )
                return

        elif new_challenge_number is None:
            print("[warn] Novo item não tem challengeNumber definido; Não é possivel continuar.")
            raise  

        # 5) Atualiza a fila: insere novo item, ordena, deduplica, limita a 24
        queue.insert(0, new_item)
        queue = sort_and_dedupe_by_challenge_id_desc(queue)
        queue = keep_at_most_24(queue)

        # 6) Atualiza a descrição com base em challengeNumber
        base_desc = description or DEFAULT_DESC
        new_description = update_description_range(base_desc, queue)
        print(f"[info] Novo desafio encontrado: {new_challenge_number}! Descrição do Gist atualizada para: {new_description}")

        # 7) Serializa JSON final na estrutura:
        #    { "challenge_queue": [ ... ] }
        new_content_obj = {"challenge_queue": queue}
        new_content_str = json.dumps(new_content_obj, ensure_ascii=False, indent=2)

        # 8) Modo teste: salva só em arquivo local (com backup)
        if LOCAL_GIST_FILE and os.path.exists(LOCAL_GIST_FILE):
            backup = LOCAL_GIST_FILE + ".bak." + time.strftime("%Y%m%d-%H%M%S")
            with open(backup, "w", encoding="utf-8") as f:
                f.write(content_str)
            with open(LOCAL_GIST_FILE, "w", encoding="utf-8") as f:
                f.write(new_content_str)
            print(f"[success] Arquivo local atualizado (backup em {backup}).")
            return

        # 9) Atualiza o Gist real
        assert GITHUB_TOKEN and GIST_ID, "Defina GITHUB_TOKEN e GIST_ID para atualizar o Gist."
        resp = patch_gist(file_name, new_content_str, new_description)
        print("[success] Gist atualizado em:", resp.get("html_url"))

    except Exception as e:
        print("[error] Exceção durante execução:", e)
        traceback.print_exc()
        raise


def next_minute_in(minutes_list, now):
    """
    Retorna o próximo horário FUTURO em que 'minute' está dentro
    da lista minutes_list.

    Exemplo: minutes_list = [3,13,23,22,43,53]

    - Se agora for 05:12 -> 05:13
    - Se agora for 05:13:00 exato -> 05:13
    - Se agora for 05:13:00.100 -> 05:23
    - Se já passou de todos os minutos da lista na hora atual,
      agenda para a próxima hora usando o primeiro minuto da lista.
    """
    minutes_list = sorted(minutes_list)

    # Zera segundos/micros para construir candidatos limpos
    base = now.replace(second=0, microsecond=0)

    # Tenta encontrar um minuto ainda nesta hora que seja ESTRITAMENTE futuro
    for m in minutes_list:
        candidate = base.replace(minute=m)
        if candidate > now:
            return candidate

    # Se todos os minutos desta hora já passaram, vai para a próxima hora
    base_next_hour = base + datetime.timedelta(hours=1)
    candidate = base_next_hour.replace(minute=minutes_list[0])
    return candidate


def run_with_internal_cron():
    """
    Cron interno:
    - Executa main() imediatamente na inicialização.
    - Depois agenda para o próximo HH:03.
    - Depois roda a cada 10 minutos nos minutos [03,13,23,22,43,53].
    """

    RUN_MINUTES = [3,13,23,22,43,53]

    print("[cron] Execução imediata na inicialização...")
    main()

    # Primeiro agendamento será o próximo minuto válido da lista RUN_MINUTES
    now = datetime.datetime.now()
    first_run = next_minute_in(RUN_MINUTES, now)

    delta = (first_run - now).total_seconds()
    print(f"[cron] Aguardando até primeira execução programada: {first_run} (em {delta:.2f}s)")
    time.sleep(max(0, delta))

    print(f"[cron] Executando main() no primeiro minuto 05 ({datetime.datetime.now()})")
    main()

    # Agora entra no cron fixo de 10 em 10 minutos nos minutos pré-definidos
    while True:
        now = datetime.datetime.now()
        next_run = next_minute_in(RUN_MINUTES, now)
        delta = (next_run - now).total_seconds()

        print(f"[cron] Próxima execução programada para {next_run.strftime('%Y-%m-%d %H:%M:%S')} (em {delta:.2f}s)")
        time.sleep(max(0, delta))

        print(f"[cron] Executando main() ({datetime.datetime.now()})")
        main()


if __name__ == "__main__":
    run_with_internal_cron()
