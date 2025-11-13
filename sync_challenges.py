# -*- coding: utf-8 -*-
"""
Sincroniza a lista `challenge_queue` do arquivo padrão (challenges_from_the_last_24_hours.json)
para todos os arquivos `*scavenger-mine-export-*.json` no mesmo diretório.

# Padrão: backups com sufixo `.json.bkp`
python sync_challenges.py

# Manter a extensão original `.json` nos backups
python sync_challenges.py --keep-extension

# Não criar backups
python sync_challenges.py --no-backup

# Informando caminhos personalizados
python sync_challenges.py --source /caminho/challenge.json --targets "/caminho/*scavenger-mine-export-*.json"

# Escolher a pasta de backup
python sync_challenges.py --backup-dir "/caminho/backup"

# Dry run (mostra o que faria, sem escrever nada)
python sync_challenges.py --dry-run
"""

import os
import json
import glob
import shutil
import datetime
import argparse
import sys


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def ensure_challenge_queue(data, path_label):
    if "challenge_queue" not in data or not isinstance(data["challenge_queue"], list):
        raise ValueError(f"O arquivo '{path_label}' não possui uma lista 'challenge_queue' válida.")
    return data["challenge_queue"]


def main():
    parser = argparse.ArgumentParser(description="Sincronizar 'challenge_queue' entre JSONs.")
    parser.add_argument("--source", default="challenges_from_the_last_24_hours.json", help="Caminho para o JSON padrão.")
    parser.add_argument("--targets", default="*scavenger-mine-export-*.json", help="Glob para arquivos alvo.")
    parser.add_argument("--backup-dir", default=None, help="Diretório de backup (default: cria backup-YYYYmmdd-HHMMSS).")
    parser.add_argument("--dry-run", action="store_true", help="Apenas mostra o que seria feito, sem escrever nada.")
    parser.add_argument(
        "--keep-extension",
        action="store_true",
        help="Mantém a extensão original (.json) nos backups, ao invés de usar .json.bkp."
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Não cria arquivos de backup antes de sobrescrever os JSONs."
    )
    args = parser.parse_args()

    base_dir = os.path.abspath(os.path.dirname(args.source))
    source_path = os.path.abspath(args.source)

    if not os.path.exists(source_path):
        print(f"ERRO: arquivo padrão não encontrado: {source_path}", file=sys.stderr)
        sys.exit(1)

    try:
        source_data = load_json(source_path)
        source_queue = ensure_challenge_queue(source_data, source_path)
    except Exception as e:
        print(f"ERRO ao ler '{source_path}': {e}", file=sys.stderr)
        sys.exit(1)

    # Resolver padrão dos alvos
    targets_pattern = args.targets
    # Normalização automática de caminhos relativos
    if not os.path.isabs(targets_pattern) and not targets_pattern.startswith("./"):
        if not glob.glob(targets_pattern):
            alt_pattern = f"./{targets_pattern}"
            if glob.glob(alt_pattern):
                targets_pattern = alt_pattern
    if not os.path.isabs(targets_pattern):
        targets_pattern = os.path.join(base_dir, targets_pattern)

    targets = sorted(glob.glob(targets_pattern))
    targets = [p for p in targets if os.path.abspath(p) != source_path]

    if not targets:
        print(f"Aviso: nenhum arquivo alvo encontrado com o padrão: {targets_pattern}")
        sys.exit(0)

    # Preparar backup
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = args.backup_dir or os.path.join(base_dir, f"backup-{ts}")

    if not args.no_backup and not args.dry_run:
        os.makedirs(backup_dir, exist_ok=True)

    log_path = os.path.join(base_dir, f"substituicao-challenge-queue-log-{ts}.jsonl")
    rows = []

    for path in targets:
        try:
            data = load_json(path)
            old_list = data.get("challenge_queue", [])
            old_count = len(old_list) if isinstance(old_list, list) else 0

            # Nome de backup flexível
            filename = os.path.basename(path)
            if args.keep_extension:
                backup_name = filename
            else:
                backup_name = f"{filename}.bkp"
            backup_path = os.path.join(backup_dir, backup_name)

            if not args.no_backup and not args.dry_run:
                shutil.copy2(path, backup_path)

            # Mesclar (não substituir): construir o TOPO exatamente na ordem do arquivo fonte.
            # Para cada challengeId na fonte:
            #   - se existir no destino, mantém o item do destino (status preservado)
            #   - se não existir, insere o item da fonte (preenche faltantes)
            # Depois, acrescenta os itens do destino que não estão na fonte, preservando a ordem relativa deles.
            target_queue = data.get("challenge_queue", [])
            # Índices úteis
            existing_by_id = {}
            for item in target_queue:
                if isinstance(item, dict) and "challengeId" in item:
                    existing_by_id[str(item.get("challengeId"))] = item

            source_ids = []
            for s in source_queue:
                if isinstance(s, dict) and "challengeId" in s:
                    source_ids.append(str(s.get("challengeId")))

            # Constrói a parte superior em ordem decrescente por challengeId (ex.: 334 -> 311)
            # Mapa da fonte por ID para resgatar o item quando não existe no destino
            source_by_id = {}
            for s in source_queue:
                if isinstance(s, dict) and "challengeId" in s:
                    source_by_id[str(s.get("challengeId"))] = s

            top_section = []
            try:
                sorted_ids = sorted(source_ids, key=lambda x: int(x), reverse=True)
            except Exception:
                # fallback: ordenação lexicográfica se não forem inteiros
                sorted_ids = sorted(source_ids, reverse=True)
            for cid in sorted_ids:
                if cid in existing_by_id:
                    top_section.append(existing_by_id[cid])  # mantém o que já existia (pode estar 'validated', etc.)
                else:
                    # usa o item da fonte correspondente
                    if cid in source_by_id:
                        top_section.append(source_by_id[cid])

            # Agora, itens do destino que não estão na fonte (preserva a ordem original entre eles)
            rest_section = []
            source_id_set = set(source_ids)
            for item in target_queue:
                cid = str(item.get("challengeId")) if isinstance(item, dict) and "challengeId" in item else None
                if cid is None or cid not in source_id_set:
                    rest_section.append(item)

            data["challenge_queue"] = top_section + rest_section
            new_count = len(data["challenge_queue"])

            if not args.dry_run:
                save_json(path, data)

            rows.append({
                "arquivo": filename,
                "backup": None if args.no_backup else os.path.abspath(backup_path),
                "itens_antes": old_count,
                "itens_depois": new_count,
                "status": "ok" if not args.dry_run else "dry-run"
            })
            print(f"[OK] {filename}: {old_count} -> {new_count}")

        except Exception as e:
            rows.append({
                "arquivo": os.path.basename(path),
                "backup": None,
                "status": f"erro: {e}"
            })
            print(f"[ERRO] {os.path.basename(path)}: {e}", file=sys.stderr)

    if not args.dry_run:
        with open(log_path, "w", encoding="utf-8") as logf:
            for r in rows:
                logf.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("-" * 60)
    print(f"Arquivos processados: {len(targets)}")
    if args.no_backup:
        print("Backups: DESATIVADOS (--no-backup)")
    else:
        print(f"Backups em: {os.path.abspath(backup_dir)}")
    print(f"Log: {os.path.abspath(log_path)}")


if __name__ == "__main__":
    main()
