"""
run_crdb_extended.py — Collecte étendue CRDB pour Paper 5
Objectif : n=15 par condition (0, 100, 500, 1000 TPS)
pour satisfaire exigence Q1 + test TOST d'invariance.

Usage :
    python run_crdb_extended.py

Les fichiers JSON existants (crdb_tps*.json) sont chargés
automatiquement — seuls les trials manquants sont ajoutés.
"""

import time
import json
import os
import sys

# ── Import votre module existant ──────────────────────────────────
try:
    from measure_crdb import run_session, start_port_forward, PODS
except ImportError:
    print("ERREUR : measure_crdb.py introuvable dans le répertoire courant.")
    sys.exit(1)

# ── Paramètres ────────────────────────────────────────────────────
TARGET_N   = 15          # trials visés par condition
CONDITIONS = [0, 100, 500, 1000]
FILES = {
    0:    'crdb_tps0.json',
    100:  'crdb_tps100.json',
    500:  'crdb_tps500.json',
    1000: 'crdb_tps1000.json',
}
INTER_CONDITION_PAUSE = 180  # secondes entre conditions

# ── Utilitaires ───────────────────────────────────────────────────
def load_existing(filepath):
    """Charge les trials existants depuis un fichier JSON."""
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            data = json.load(f)
        print(f"  Chargé {len(data)} trials existants depuis {filepath}")
        return data
    return []

def save(filepath, results):
    with open(filepath, 'w') as f:
        json.dump(results, f, indent=2)

def trials_needed(tps, filepath):
    existing = load_existing(filepath)
    n_existing = len(existing)
    n_needed   = max(0, TARGET_N - n_existing)
    return n_existing, n_needed, existing

# ── Collecte ──────────────────────────────────────────────────────
def collect():
    print("=" * 60)
    print(f"  Collecte étendue CRDB — objectif n={TARGET_N} / condition")
    print("=" * 60)

    # Vérification résumée avant de lancer
    print("\nÉtat actuel :")
    total_needed = 0
    for tps in CONDITIONS:
        n_ex, n_need, _ = trials_needed(tps, FILES[tps])
        status = "OK" if n_need == 0 else f"manque {n_need}"
        print(f"  TPS={tps:4d}  existants={n_ex:2d}  [{status}]")
        total_needed += n_need

    if total_needed == 0:
        print("\nTous les trials sont déjà collectés. Rien à faire.")
        return

    estimated_min = (total_needed * 120 + (total_needed // TARGET_N) * INTER_CONDITION_PAUSE) // 60
    print(f"\nTotal trials à ajouter : {total_needed}")
    print(f"Durée estimée : ~{estimated_min} minutes")
    print("\nDémarrage dans 5 secondes (Ctrl+C pour annuler)...")
    time.sleep(5)

    # S'assurer que le port-forward est actif
    start_port_forward(PODS[0])
    time.sleep(2)

    for i, tps in enumerate(CONDITIONS):
        n_ex, n_need, existing = trials_needed(tps, FILES[tps])

        if n_need == 0:
            print(f"\n[TPS={tps}] Déjà {n_ex} trials — ignoré.")
            continue

        print(f"\n{'='*60}")
        print(f"[TPS={tps}] Collecte de {n_need} trials supplémentaires")
        print(f"{'='*60}")

        # run_session renvoie les nouveaux résultats
        # On crée un fichier temporaire pour les nouveaux trials
        tmp_file = f"crdb_tps{tps}_new.json"
        new_results = run_session(tps, n_need, tmp_file)

        # Renuméroter les trials pour éviter les doublons
        offset = n_ex
        for r in new_results:
            r['trial'] = r['trial'] + offset

        # Fusionner et sauvegarder
        merged = existing + new_results
        save(FILES[tps], merged)
        print(f"\n  Fusion : {n_ex} + {len(new_results)} = {len(merged)} trials")
        print(f"  Sauvegardé → {FILES[tps]}")

        # Supprimer le fichier temporaire
        if os.path.exists(tmp_file):
            os.remove(tmp_file)

        # Pause entre conditions (sauf après la dernière)
        if i < len(CONDITIONS) - 1:
            remaining = sum(
                max(0, TARGET_N - len(load_existing(FILES[c])))
                for c in CONDITIONS[i+1:]
            )
            if remaining > 0:
                print(f"\n  Pause inter-condition : {INTER_CONDITION_PAUSE}s...")
                time.sleep(INTER_CONDITION_PAUSE)

    print("\n" + "="*60)
    print("  Collecte terminée. Fichiers produits :")
    for tps in CONDITIONS:
        data = load_existing(FILES[tps])
        rtos = [r['rto'] for r in data if r]
        if rtos:
            mean_rto = sum(rtos) / len(rtos)
            print(f"  TPS={tps:4d}  n={len(data):2d}  RTO moyen={mean_rto:.3f}s")
    print("="*60)
    print("\nProchaine étape : python analyze_tost.py")

if __name__ == '__main__':
    collect()
