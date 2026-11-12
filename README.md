# hab-phase-decomp

Raw data and scripts for the paper

> I. Jerrari and I. Assayad, *Load-Dependent Failover Bottlenecks in Kubernetes-Deployed Databases:
> A Phase-Decomposition Study of PXC/Galera and CockroachDB* (submitted).

The study measures the phases of the Recovery Time Objective (RTO) after the primary pod of a replicated
database is deleted on Kubernetes, with and without concurrent write load. Systems: Percona XtraDB Cluster
(PXC/Galera, 81 trials in three campaigns) and CockroachDB (59 trials). Testbed: kind / Kubernetes on a single
Windows 11 workstation (WSL2). The zero-load methodology and the phase taxonomy come from the companion
technical report HABench (Jerrari and Assayad, LIS-2026-01, 2026).

## Quick start: reproduce the statistics

```bash
pip install -r requirements.txt
python analysis/reproduce_statistics.py
```

The script reads the raw `.json` files under `data/` and recomputes the descriptive statistics and the
Mann-Whitney, Kruskal-Wallis, Spearman and Fisher tests reported in Sections 6 and 7 of the paper. It first checks
the trial counts (81 PXC/Galera + 59 CockroachDB = 140).

## Repository layout

```
hab-phase-decomp/
├── README.md
├── LICENSE                        Apache License 2.0
├── CITATION.cff
├── requirements.txt
├── analysis/
│   └── reproduce_statistics.py    recomputes the paper's statistics from data/
├── data/
│   ├── MANIFEST.csv               one row per file: system, campaign, generator, threads, trials, used in paper
│   ├── pxc/
│   │   ├── v1_per_connection/     campaign v1 (28 trials)
│   │   ├── v3_session1/           campaign v3, "session 1" (28 trials)
│   │   └── extra_session2/        campaign extra, "session 2" (25 trials)
│   ├── cockroachdb/               59 trials
│   └── not_used_in_paper/         preliminary runs, backups, an earlier analysis
├── scripts/                       measurement, orchestration and cleaning scripts
└── manifests/
    ├── pxc/                       PXC/Galera cluster manifest
    └── moco/                      MOCO manifests (not used in the paper)
```

## Campaigns

| Campaign (paper name) | Folder | Generator | Writer threads | Trials |
|---|---|---|---|---|
| PXC v1 | `data/pxc/v1_per_connection/` | per-connection (`measure_pxc.py`) | 0 / 4 / 16 / 32 (targets 0 / 100 / 500 / 1000 TPS) | 7 + 7 + 7 + 7 = 28 |
| PXC v3 = session 1 | `data/pxc/v3_session1/` | connection pool (`measure_pxc_v3.py`) | 0 / 16 / 32 / 64 (same targets) | 7 + 7 + 7 + 7 = 28 |
| PXC extra = session 2 | `data/pxc/extra_session2/` | connection pool (`measure_pxc_v3.py`) | 0 / 16 | 12 + 13 = 25 |
| CockroachDB | `data/cockroachdb/` | psycopg2 pool (`measure_crdb.py`) | 0 / 8 / 16 / 16 (targets 0 / 100 / 500 / 1000 TPS) | 15 + 15 + 14 + 15 = 59 |

The pooled zero-load group of the paper (n = 19) is `v3_tps0.json` + `v3_tps0_extra.json`. Sessions 1 and 2 used the
same generator and were run at different times. The thread count is derived from the target by the generators, so
the two are confounded within a campaign (see the paper, Section 10).

## Data fields

| Field | Meaning |
|---|---|
| `trial` | trial index within the file |
| `tps_target` | target write rate of the load generator (TPS) |
| `n_threads` | number of writer threads (not stored in the v1 files: `measure_pxc.py` uses 4 threads for targets up to 100, 16 up to 500, 32 above) |
| `t1` | seconds from pod deletion to cluster ready / new leader (phase "detection and eviction") |
| `t3` | seconds from `t1` to the first successful write (phase "data-path readiness") |
| `t3_raw`, `pf_delay` | raw `t3` and the port-forward reconnection delay subtracted from it (v3 and extra files only) |
| `rto` | recovery time: pod deletion to first successful write, as recorded by the script |
| `rpo` | percentage of acknowledged writes lost (0 in all 140 trials) |
| `writes_ok`, `writes_fail` | writes acknowledged / failed during the trial |
| `tps_actual` | `writes_ok / rto`: **an outcome of the trial, not the offered load** (it falls when the recovery is long) |

The service-switchover phase `t2` is excluded as load-independent (paper, Section 2). In the paper, trials with
`t1 > 40 s` form the "slow-recovery regime".

## Scripts

The scripts import each other and write their `.json` output to the current directory, so run them from inside
`scripts/` and copy the results to `data/` afterwards.

- `measure_pxc.py`, `run_experiments.py`: PXC/Galera, campaign v1.
- `measure_pxc_v2.py`, `run_experiments_v2.py`: PXC/Galera, earlier campaign v2 (not used in the paper).
- `measure_pxc_v3.py`: PXC/Galera, connection-pool generator (sessions 1 and 2). `run_experiments_v3.py` is a shell
  one-liner (`python -c "..."`) rather than a Python module: copy the block between the quotes.
- `measure_crdb.py`, `run_crdb_extended.py`: CockroachDB.
- `fix_crdb_data.py`, `fix_crdb_data_v2.py`: cleaning of the CockroachDB files (fixed 30 s threshold, see below).
- `analyze_tost.py`: an earlier equivalence-test analysis of the CockroachDB data (not used in the paper).
- `measure_moco.py`: MOCO experiments (future work).

## Excluded trials

- CockroachDB, zero load: two trials with RTO > 30 s were removed with a fixed 30 s threshold
  (`scripts/fix_crdb_data_v2.py`) as measurement-layer failures (port-forward reconnection to several pods in sequence).
  The records of these two trials are not part of this repository.
- PXC/Galera, session 2: one load trial (15.1 TPS; `t1` = 6.6 s, `t3` = 19.8 s, RTO = 26.4 s) belongs to neither the nominal
  nor the slow class. It is kept in `v3_tps100_extra.json` and handled in a sensitivity analysis (paper, Section 6.3).

## Files not used in the paper's statistics (`data/not_used_in_paper/`)

Preliminary PXC/Galera runs (`pxc_pilot_runs/`), the earlier campaign v2 (`pxc_campaign_v2/`), earlier versions of the
CockroachDB files (`cockroachdb_backups/`) and the output of an earlier analysis (`earlier_analysis/`).

## Known gaps

The CockroachDB StatefulSet manifest and the kind cluster configuration used for the experiments are not included.
The deployment is described in Table 4 of the paper (3-pod StatefulSet, CockroachDB v23.1.11, heartbeat 500 ms, 2 Gi PVC
per pod; PXC Operator v1.14.0, PXC 8.0.36-28.1, `evs.suspect_timeout` = 5 s, kind v0.20.0).

## Notes and limits

- The three PXC campaigns differ at zero load (mean `t3` 0.90 s in v1, 0.60 s in session 1, 1.48 s in session 2); the paper
  compares load with zero load within each campaign.
- The port-forward delay is subtracted per trial in the v3 and extra files; the v1 script does not record it.
- All experiments ran on one workstation: absolute values are not production values.

## License and citation

Apache License 2.0 (`LICENSE`). Please cite the paper above (see `CITATION.cff`).

## Contact

Imane Jerrari, Laboratory of Information Systems (LIS), Hassan II University of Casablanca,
imane.jerrari-etu@etu.univh2c.ma
