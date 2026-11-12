"""Reproduces the statistics of Sections 6 and 7 of the paper from the raw trial files
(PXC/Galera: campaigns v1, v3 = session 1, extra = session 2; CockroachDB).

Usage : python analysis/reproduce_statistics.py [repository root]  (default: parent folder of this script)
Pooled zero-load group n=19, pooled nominal load group n=13, then load vs zero load tested
within each session / campaign.
"""
import json, math, sys, statistics as st
from scipy import stats

import os
root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# index des fichiers de données : nom sans extension -> chemin (sous data/)
_INDEX = {}
for _d, _, _fs in os.walk(os.path.join(root, "data")):
    for _f in _fs:
        if _f.endswith(".json"):
            _INDEX[_f[:-5]] = os.path.join(_d, _f)
L = lambda k: json.load(open(_INDEX[k]))

# Contrôle des effectifs : 81 essais PXC/Galera + 59 essais CockroachDB = 140
_pxc = [L(k) for k in ("results_pxc_tps0_clean","results_pxc_tps100_clean","results_pxc_tps500_clean","results_pxc_tps1000_clean",
                       "v3_tps0","v3_tps100","v3_tps500","v3_tps1000","v3_tps0_extra","v3_tps100_extra")]
_crdb = [L(f"crdb_tps{t}") for t in (0, 100, 500, 1000)]
print(f"Effectifs : PXC/Galera {sum(map(len,_pxc))} essais, CockroachDB {sum(map(len,_crdb))} essais, total {sum(map(len,_pxc))+sum(map(len,_crdb))}")
z1, z2 = L("v3_tps0"), L("v3_tps0_extra")            # zéro-charge, session 1 (7) et session 2 (12)
a, e = L("v3_tps100"), L("v3_tps100_extra")           # 16 threads, cible 100 TPS : session 1 (7) et session 2 (13)
z = z1 + z2
# Reconstitution du groupe "nominal" du Tableau 6 : session 1 (7) + session 2 hors #3 (t3=19,8 s) et hors t1>40 s
e_nom = [x for x in e if x["trial"] != 3 and x["t1"] < 40]
nom = a + e_nom
e_high = [x for x in e if x["t1"] > 40]


def mean(d, k):
    return st.mean(x[k] for x in d)


def describe(name, d):
    ta = [x["tps_actual"] for x in d]
    print(f"{name:38s} n={len(d):2d} TPS réel moy={mean(d,'tps_actual'):6.1f} [{min(ta):.1f}-{max(ta):.1f}] "
          f"t1={mean(d,'t1'):.3f} t3={mean(d,'t3'):.3f} RTO={mean(d,'rto'):.3f}")


def mw(name, x, y, k):
    u = stats.mannwhitneyu([i[k] for i in x], [i[k] for i in y], alternative="two-sided")
    n1, n2 = len(x), len(y)
    umin = min(u.statistic, n1 * n2 - u.statistic)
    zz = (umin - n1 * n2 / 2) / math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    print(f"  MW {name:44s} {k}: U={u.statistic:.0f} p={u.pvalue:.4f} r={abs(zz)/math.sqrt(n1+n2):.2f}")


print("== Descriptif")
describe("zéro-charge (19)", z)
describe("  zéro session 1", z1)
describe("  zéro session 2", z2)
describe('pooled load, nominal (13)', nom)
describe("  session 1 : v3_tps100", a)
describe("  session 2 : extra nominal (6)", e_nom)
print("Essai exclu (extra #3) :", [(x["tps_actual"], x["t1"], x["t3"], x["rto"]) for x in e if x["trial"] == 3])
print("Essais t1>40 s dans les fichiers v3 :", len(e_high), "sur", len(a) + len(e), "essais à 16 threads")
print("  session 1 (v3_tps100) :", sum(x["t1"] > 40 for x in a), "/", len(a),
      "| session 2 (extra) :", sum(x["t1"] > 40 for x in e), "/", len(e))

print("\n== Tests (Mann-Whitney bilatéral)")
mw("zéro (19) vs nominal (13) [pooled, Sec. 6]", z, nom, "t3")
mw("zéro (19) vs nominal (13)", z, nom, "t1")
mw("zéro (19) vs nominal (13)", z, nom, "rto")
mw("session 1 : zéro (7) vs ~90 TPS (7)", z1, a, "t3")
mw("session 1 : zéro (7) vs ~90 TPS (7)", z1, a, "t1")
mw("session 2 : zéro (12) vs ~31 TPS (6)", z2, e_nom, "t3")
mw("session 2 : zéro (12) vs ~31 TPS (6)", z2, e_nom, "t1")

print("\n== Dose-réponse")
r = stats.spearmanr([x["tps_actual"] for x in nom], [x["t3"] for x in nom])
print(f"  Spearman t3 vs TPS réel, groupe nominal (13) : rho={r.statistic:.2f} p={r.pvalue:.5f}")
print("  Ratio t3 charge/zéro, session 1 : %.2fx ; session 2 : %.2fx"
      % (mean(a, "t3") / mean(z1, "t3"), mean(e_nom, "t3") / mean(z2, "t3")))
print("  pf_delay moyen : session 1 zéro %.2f / ~90 TPS %.2f ; session 2 zéro %.2f / ~31 TPS %.2f"
      % (mean(z1, "pf_delay"), mean(a, "pf_delay"), mean(z2, "pf_delay"), mean(e_nom, "pf_delay")))


# ---------------------------------------------------------------------------
# Dose-réponse sur le nombre de threads (campagne session 1 : zéro, 16, 32, 64)
# ---------------------------------------------------------------------------
t32, t64 = L("v3_tps500"), L("v3_tps1000")
print("\n== Threads (campagne session 1)")
lvl = [(0, z1), (16, a), (32, t32), (64, t64)]
for th, d in lvl:
    print(f"  {th:2d} threads n={len(d)} t3 médiane={st.median(x['t3'] for x in d):.2f} "
          f"moy={mean(d,'t3'):.2f} max={max(x['t3'] for x in d):.2f} RTO moy={mean(d,'rto'):.2f}")
pairs = [(th, x["t3"]) for th, d in lvl for x in d]
r = stats.spearmanr([p for p, _ in pairs], [q for _, q in pairs])
print(f"  Spearman threads vs t3 (n={len(pairs)}): rho={r.statistic:.2f} p={r.pvalue:.2g}")
kw = stats.kruskal(*[[x['t3'] for x in d] for _, d in lvl[1:]])
print(f"  Kruskal-Wallis t3 entre 16/32/64 threads : H={kw.statistic:.2f} p={kw.pvalue:.3f}")

# ---------------------------------------------------------------------------
# CockroachDB
# ---------------------------------------------------------------------------
print("\n== CockroachDB")
C = {k: L(f"crdb_tps{k}") for k in (0, 100, 500, 1000)}
for k, d in C.items():
    r_ = [x["rto"] for x in d]
    print(f"  cible {k:4d} n={len(d)} threads={sorted(set(x['n_threads'] for x in d))} "
          f"médiane={st.median(r_):.3f} moy={st.mean(r_):.3f} SD={st.stdev(r_):.3f}")
kw = stats.kruskal(*[[x["rto"] for x in d] for d in C.values()])
print(f"  Kruskal-Wallis : H={kw.statistic:.2f} p={kw.pvalue:.3f}")
zc = [x["rto"] for x in C[0]]
lc = [x for k in (100, 500, 1000) for x in C[k]]
u = stats.mannwhitneyu(zc, [x["rto"] for x in lc], alternative="two-sided")
print(f"  zéro vs charge poolée (n={len(lc)}): médiane {st.median(zc):.3f} -> {st.median(x['rto'] for x in lc):.3f}, p={u.pvalue:.4f}")
print(f"  écritures échouées (charge) : {sum(x['writes_fail'] for x in lc)}/{sum(x['writes_fail']+x['writes_ok'] for x in lc)}")
print("  NB : tps_actual = writes_ok / RTO (voir measure_*.py) : c'est un résultat de l'essai, pas la charge offerte.")


# ---------------------------------------------------------------------------
# Campagne v1 (générateur per-connection ; cibles 100/500/1000 -> 4/16/32 threads)
# et régime lent (t1 > 40 s) sur les trois campagnes
# ---------------------------------------------------------------------------
print("\n== Campagne v1")
v1 = {0: L("results_pxc_tps0_clean"), 4: L("results_pxc_tps100_clean"),
      16: L("results_pxc_tps500_clean"), 32: L("results_pxc_tps1000_clean")}
for th, d in v1.items():
    line = (f"  {th:2d} threads n={len(d)} t3 médiane={st.median(x['t3'] for x in d):.2f} "
            f"moy={mean(d,'t3'):.2f} t1 moy={mean(d,'t1'):.2f} RTO moy={mean(d,'rto'):.2f} "
            f"lents={sum(x['t1']>40 for x in d)}")
    if th:
        u = stats.mannwhitneyu([x['t3'] for x in v1[0]], [x['t3'] for x in d], alternative='two-sided')
        line += f" | t3 vs zéro p={u.pvalue:.4f}"
    print(line)
pr = [(th, x["t3"]) for th, d in v1.items() for x in d]
r = stats.spearmanr([p for p, _ in pr], [q for _, q in pr])
print(f"  Spearman threads vs t3 (v1, n={len(pr)}): rho={r.statistic:.2f} p={r.pvalue:.2g}")
kw = stats.kruskal(*[[x['t3'] for x in v1[k]] for k in (4, 16, 32)])
print(f"  Kruskal-Wallis t3 entre 4/16/32 threads : H={kw.statistic:.2f} p={kw.pvalue:.4f}")

print("\n== Régime lent (t1 > 40 s)")
t16 = v1[16] + a + e                      # 16 threads : v1, session 1, session 2
other = v1[4] + v1[32] + t32 + t64        # autres charges : 4, 32, 64 threads
s16 = sum(x["t1"] > 40 for x in t16); so = sum(x["t1"] > 40 for x in other)
f = stats.fisher_exact([[s16, len(t16) - s16], [so, len(other) - so]])
print(f"  16 threads : {s16}/{len(t16)} lents ; autres charges : {so}/{len(other)} ; Fisher p={f.pvalue:.4f}")
print(f"  par campagne : v1 {sum(x['t1']>40 for x in v1[16])}/7, session 1 {sum(x['t1']>40 for x in a)}/7, "
      f"session 2 {sum(x['t1']>40 for x in e)}/13 ; zéro-charge : "
      f"{sum(x['t1']>40 for x in v1[0]+z1+z2)}/{len(v1[0]+z1+z2)}")
