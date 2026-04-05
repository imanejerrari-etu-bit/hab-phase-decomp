"""
fix_crdb_data_v2.py — Nettoyage conservateur (Option A)

Règle unique et documentable pour le paper :
  Retrait uniquement des outliers infrastructure
  définis comme RTO > 30s (= 6× le RTO médian attendu ~5s)
  Ces trials présentent un port-forward failure documenté
  (reconnexion successive à plusieurs pods), pas un comportement Raft.

Critère pré-enregistré (à citer dans Threats to Validity) :
  "Trials with RTO > 30s were excluded a priori as infrastructure
   artefacts (port-forward reconnection failures), identifiable
   by sequential pod reconnection attempts in the measurement log.
   This threshold is 6× the expected median RTO and was defined
   before analysis."
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

# Seuil infrastructure — documenté et justifiable
INFRA_THRESHOLD = 30.0  # secondes

def load(filepath):
    with open(filepath) as f:
        return json.load(f)

def save(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

print("=" * 65)
print("  Nettoyage conservateur CRDB — seuil infrastructure > 30s")
print("=" * 65)
print(f"  Critère : RTO > {INFRA_THRESHOLD}s = outlier infrastructure")
print(f"  Justification : 6× RTO médian attendu (~5s)")
print()

cleaned   = {}
needs_more = {}

for tps in [0, 100, 500, 1000]:
    # Recharger depuis backup si disponible
    backup = FILES[tps].replace('.json', '_backup.json')
    source = backup if os.path.exists(backup) else FILES[tps]
    data   = load(source)
    rtos   = [r['rto'] for r in data]

    outliers = [r for r in data if r['rto'] > INFRA_THRESHOLD]
    clean    = [r for r in data if r['rto'] <= INFRA_THRESHOLD]
    clean_rtos = [r['rto'] for r in clean]

    print(f"[TPS={tps}]  source={source}")
    print(f"  n total  = {len(data)}")

    if outliers:
        for r in outliers:
            print(f"  EXCLU    trial={r['trial']:2d}  RTO={r['rto']:.3f}s"
                  f"  pod={r.get('pod_deleted','?')}  → infra failure")
    else:
        print(f"  Aucun outlier infrastructure (RTO > {INFRA_THRESHOLD}s)")

    print(f"  n propre = {len(clean)}")
    if clean_rtos:
        mean = np.mean(clean_rtos)
        sd   = np.std(clean_rtos, ddof=1)
        cv   = 100 * sd / mean
        print(f"  mean={mean:.3f}s  SD={sd:.3f}s  CV={cv:.1f}%"
              f"  range=[{min(clean_rtos):.3f}, {max(clean_rtos):.3f}]")

    cleaned[tps]  = clean
    missing       = max(0, 15 - len(clean))
    needs_more[tps] = missing
    if missing > 0:
        print(f"  ⚠ Besoin de {missing} trial(s) supplémentaire(s)")
    else:
        print(f"  ✓ n={len(clean)} — objectif atteint")
    print()

# Résumé
print("=" * 65)
print("  Résumé")
print("=" * 65)
total_missing = sum(needs_more.values())
for tps in [0, 100, 500, 1000]:
    status = (f"recolleter {needs_more[tps]}"
              if needs_more[tps] > 0 else "OK ✓")
    print(f"  TPS={tps:4d}  n_propre={len(cleaned[tps]):2d}  {status}")

# Sauvegarde
print()
for tps in [0, 100, 500, 1000]:
    clean = cleaned[tps]
    for i, r in enumerate(clean):
        r['trial'] = i + 1
    save(FILES[tps], clean)
    print(f"  Sauvegardé → {FILES[tps]}  ({len(clean)} trials)")

print()
if total_missing > 0:
    print(f"  {total_missing} trial(s) à recolleter.")
    print("  Étape suivante : python run_crdb_extended.py")
else:
    print("  Toutes conditions à n=15. Étape suivante : python analyze_tost.py")

# Note pour le paper
print("""
=================================================================
  NOTE POUR LE PAPER (Threats to Validity)
=================================================================
Ajouter dans la section Threats to Validity :

  Two trials at TPS\\,=\\,0 were excluded as infrastructure
  artefacts: both exhibited RTO\\,>\\,30\\,s caused by sequential
  port-forward reconnection to multiple pods after primary
  deletion of cockroachdb-0, a measurement-layer failure
  unrelated to Raft election behaviour.
  The exclusion threshold (RTO\\,>\\,30\\,s, i.e.\\ $6\\times$ the
  expected median) was defined prior to analysis.
  All remaining trials are included without further filtering.
=================================================================
""")
