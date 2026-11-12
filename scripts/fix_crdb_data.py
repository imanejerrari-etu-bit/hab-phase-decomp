"""
fix_crdb_data.py — Nettoyage des données CRDB avant analyse finale

Problèmes identifiés :
  1. Trial 3 TPS=0 : RTO=68.658s — outlier infrastructure
     (port-forward failure, pas un vrai comportement Raft)
     → à retirer et remplacer par 1 nouveau trial
  2. TPS=500 : n=12 seulement (3 échecs connection refused)
     → à compléter jusqu'à n=15

Ce script :
  - Identifie et exclut les outliers infrastructure (IQR method)
  - Montre les stats avant/après nettoyage
  - Prépare la re-collecte ciblée
"""

import json
import os
import numpy as np

FILES = {
    0:    'crdb_tps0.json',
    100:  'crdb_tps100.json',
    500:  'crdb_tps500.json',
    1000: 'crdb_tps1000.json',
}

def load(filepath):
    with open(filepath) as f:
        return json.load(f)

def save(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

def iqr_outliers(rtos):
    """Détecte les outliers par méthode IQR (Tukey, k=3 pour outliers extrêmes)."""
    q1, q3 = np.percentile(rtos, 25), np.percentile(rtos, 75)
    iqr    = q3 - q1
    lo     = q1 - 3 * iqr
    hi     = q3 + 3 * iqr
    return lo, hi

print("=" * 65)
print("  Diagnostic et nettoyage des données CRDB")
print("=" * 65)

cleaned = {}
needs_more = {}

for tps in [0, 100, 500, 1000]:
    data  = load(FILES[tps])
    rtos  = [r['rto'] for r in data]

    lo, hi = iqr_outliers(rtos)

    outliers  = [(i, r) for i, r in enumerate(data) if r['rto'] < lo or r['rto'] > hi]
    clean     = [r for r in data if lo <= r['rto'] <= hi]
    clean_rtos = [r['rto'] for r in clean]

    print(f"\n[TPS={tps}]")
    print(f"  n total     = {len(data)}")
    print(f"  Seuil IQR   = [{lo:.2f}s, {hi:.2f}s]")

    if outliers:
        for idx, r in outliers:
            print(f"  OUTLIER     trial={r['trial']}  RTO={r['rto']:.3f}s"
                  f"  pod_deleted={r.get('pod_deleted','?')}")
        print(f"  → {len(outliers)} outlier(s) retirés")
    else:
        print(f"  Aucun outlier détecté")

    print(f"  n propre    = {len(clean)}")
    if clean_rtos:
        print(f"  mean clean  = {np.mean(clean_rtos):.3f}s  "
              f"SD={np.std(clean_rtos, ddof=1):.3f}s  "
              f"CV={100*np.std(clean_rtos,ddof=1)/np.mean(clean_rtos):.1f}%")

    cleaned[tps] = clean
    missing = max(0, 15 - len(clean))
    if missing > 0:
        needs_more[tps] = missing
        print(f"  ⚠ Besoin de {missing} trial(s) supplémentaire(s)")
    else:
        print(f"  ✓ n={len(clean)} suffisant")

print("\n" + "=" * 65)
print("  Résumé — trials à recolleter")
print("=" * 65)

if needs_more:
    for tps, n in needs_more.items():
        print(f"  TPS={tps:4d}  → recolleter {n} trial(s)")
    print("\nLancez ensuite : python run_crdb_extended.py")
    print("(Le script détectera automatiquement les manquants)")
else:
    print("  Toutes les conditions ont n≥15 après nettoyage. ✓")

# Sauvegarde des fichiers nettoyés (backup d'abord)
print("\n" + "=" * 65)
print("  Application du nettoyage")
print("=" * 65)

for tps in [0, 100, 500, 1000]:
    original = load(FILES[tps])
    # Backup
    backup_file = FILES[tps].replace('.json', '_backup.json')
    save(backup_file, original)

    # Renuméroter proprement
    clean = cleaned[tps]
    for i, r in enumerate(clean):
        r['trial'] = i + 1
    r_outlier_note = len(original) - len(clean)

    save(FILES[tps], clean)
    print(f"  TPS={tps:4d}  backup → {backup_file}")
    print(f"          propre → {FILES[tps]}"
          f"  ({r_outlier_note} outlier(s) retirés, {len(clean)} trials)")

print("\nÉtape suivante :")
if needs_more:
    print("  1. python run_crdb_extended.py  (re-collecte ciblée)")
    print("  2. python analyze_tost.py       (analyse finale)")
else:
    print("  python analyze_tost.py  (analyse finale)")
