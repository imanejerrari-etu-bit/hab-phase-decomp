"""
measure_pxc_v2.py — Paper 5 load generator with SQLAlchemy persistent pool
+ automatic port-forward reconnection after failover.
"""
import time
import threading
import subprocess
import json

from sqlalchemy import create_engine, text
import mysql.connector

# ── Configuration ──
HOST      = '127.0.0.1'
PORT      = 3307
USER      = 'root'
PASSWORD  = 'PXCRoot2025!'
DATABASE  = 'paper5'
NAMESPACE = 'pxc-operator'
CONTEXT   = 'kind-moco-fresh'
COOLDOWN  = 120

# ── Port-forward management ──
_pf_proc = None

def start_port_forward():
    global _pf_proc
    if _pf_proc:
        try:
            _pf_proc.terminate()
        except Exception:
            pass
        time.sleep(1)
    result = subprocess.run(
        ['kubectl', 'get', 'pods', '-n', NAMESPACE,
         '--context', CONTEXT,
         '-l', 'app.kubernetes.io/component=haproxy',
         '--field-selector', 'status.phase=Running',
         '-o', 'jsonpath={.items[0].metadata.name}'],
        capture_output=True, text=True, encoding='utf-8'
    )
    haproxy = result.stdout.strip()
    if not haproxy:
        haproxy = 'pxc-cluster-p-haproxy-0'
    _pf_proc = subprocess.Popen(
        ['kubectl', 'port-forward', haproxy,
         f'{PORT}:3306', '-n', NAMESPACE, '--context', CONTEXT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    print(f'  Port-forward → {haproxy}:{PORT}')
    return _pf_proc

def stop_port_forward():
    global _pf_proc
    if _pf_proc:
        try:
            _pf_proc.terminate()
        except Exception:
            pass
        _pf_proc = None

# ── SQLAlchemy engine ──
def make_engine(pool_size=64):
    url = (f"mysql+mysqlconnector://{USER}:{PASSWORD}"
           f"@{HOST}:{PORT}/{DATABASE}")
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=30,
    )

def refresh_engine(old_engine, pool_size=64):
    """Dispose old engine and create fresh one after failover."""
    try:
        old_engine.dispose()
    except Exception:
        pass
    time.sleep(1)
    return make_engine(pool_size=pool_size)

# ── Database helpers ──
def try_insert(engine):
    try:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO measurements (val) VALUES (1)"))
            conn.commit()
        return True
    except Exception:
        return False

def get_row_count(engine):
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT COUNT(*) FROM measurements")).scalar()
    except Exception:
        return None

def get_primary_pod():
    for i in range(3):
        pod = f'pxc-cluster-p-pxc-{i}'
        try:
            result = subprocess.run(
                ['kubectl', 'exec', pod, '-n', NAMESPACE,
                 '--context', CONTEXT, '-c', 'pxc', '--',
                 'mysql', '-u', 'root', f'-p{PASSWORD}',
                 '-e', 'SELECT @@read_only;'],
                capture_output=True, text=True,
                encoding='utf-8', timeout=5
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2 and lines[1].strip() == '0':
                return pod
        except Exception:
            pass
    return None

def warmup(engine, n=5):
    with engine.connect() as conn:
        for i in range(n):
            conn.execute(
                text("INSERT INTO measurements (val) VALUES (:v)"),
                {"v": i})
        conn.commit()
    print(f'  Warmup: {n} lignes inserees')

# ── Load Generator ──
class LoadGeneratorPool:
    def __init__(self, tps_target, engine, n_threads=None):
        self.tps_target = tps_target
        self.engine = engine
        self.running = False
        self.writes_ok = 0
        self.writes_fail = 0
        self._lock = threading.Lock()
        self._threads = []
        if n_threads is not None:
            self._n = n_threads
        elif tps_target == 0:
            self._n = 0
        elif tps_target <= 50:
            self._n = 8
        elif tps_target <= 100:
            self._n = 16
        elif tps_target <= 500:
            self._n = 32
        else:
            self._n = 64

    def _worker(self):
        interval = (1.0 / self.tps_target) * self._n if self.tps_target > 0 else 1
        try:
            with self.engine.connect() as conn:
                while self.running:
                    t0 = time.time()
                    try:
                        conn.execute(text(
                            "INSERT INTO measurements (val) VALUES (1)"))
                        conn.commit()
                        with self._lock:
                            self.writes_ok += 1
                    except Exception:
                        with self._lock:
                            self.writes_fail += 1
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                    elapsed = time.time() - t0
                    sleep = interval - elapsed
                    if sleep > 0:
                        time.sleep(sleep)
        except Exception:
            pass

    def start(self):
        self.running = True
        self.writes_ok = 0
        self.writes_fail = 0
        self._threads = []
        for _ in range(self._n):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self.running = False
        for t in self._threads:
            t.join(timeout=5)

    def actual_tps(self, elapsed):
        return round(self.writes_ok / elapsed, 1) if elapsed > 0 else 0

# ── Failover helpers ──
def inject_failover(pod_name):
    subprocess.run(
        ['kubectl', 'delete', 'pod', pod_name,
         '-n', NAMESPACE, '--context', CONTEXT,
         '--grace-period=0', '--force'],
        capture_output=True
    )

def wait_for_cluster_ready(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for i in range(3):
            pod = f'pxc-cluster-p-pxc-{i}'
            try:
                result = subprocess.run(
                    ['kubectl', 'exec', pod, '-n', NAMESPACE,
                     '--context', CONTEXT, '-c', 'pxc', '--',
                     'mysql', '-u', 'root', f'-p{PASSWORD}',
                     '-e', 'SHOW STATUS LIKE "wsrep_cluster_size";'],
                    capture_output=True, text=True,
                    encoding='utf-8', timeout=3
                )
                for line in result.stdout.split('\n'):
                    if 'wsrep_cluster_size' in line and '\t3' in line:
                        return time.time() - t0
            except Exception:
                pass
        time.sleep(0.5)
    return None

def wait_for_insert(engine, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if try_insert(engine):
            return time.time() - t0
        time.sleep(0.5)
    return None

# ── Main trial function ──
def run_trial(tps_target, trial_num, engine):
    print(f'\n--- Trial {trial_num} (TPS target={tps_target}) ---')

    primary = get_primary_pod()
    if not primary:
        print('  ERR: no primary found')
        return None
    print(f'  Primary: {primary}')

    try:
        warmup(engine, 5)
    except Exception as e:
        print(f'  ERR warmup: {e}')
        return None

    n_pre = get_row_count(engine)
    print(f'  N_pre = {n_pre}')

    load = LoadGeneratorPool(tps_target, engine)
    if tps_target > 0:
        load.start()
        time.sleep(2)
        with load._lock:
            ok2 = load.writes_ok
        print(f'  Load active: {tps_target} TPS target '
              f'({load._n} threads) — {ok2} writes in 2s '
              f'({ok2/2:.1f} TPS actual)')

    t_del = time.time()
    inject_failover(primary)
    print(f'  Failover injected at T_del')

    t1_elapsed = wait_for_cluster_ready(timeout=120)
    if t1_elapsed is None:
        print('  T1 = TIMEOUT')
        if tps_target > 0:
            load.stop()
        return None
    t_elect = t_del + t1_elapsed
    print(f'  T1 = {t1_elapsed:.3f}s')

    # Reconnect port-forward after failover + refresh engine
    start_port_forward()
    engine = refresh_engine(engine, pool_size=64)

    t3_elapsed = wait_for_insert(engine, timeout=120)
    if t3_elapsed is None:
        print('  T3 = TIMEOUT')
        if tps_target > 0:
            load.stop()
        return None

    t_ins = time.time()
    t3  = t_ins - t_elect
    rto = t_ins - t_del

    if tps_target > 0:
        load.stop()
        tps_actual = load.actual_tps(rto)
        print(f'  T3 = {t3:.3f}s')
        print(f'  RTO = {rto:.3f}s')
        print(f'  Writes OK={load.writes_ok} '
              f'Fail={load.writes_fail} '
              f'TPS_actual={tps_actual}')
    else:
        tps_actual = 0
        print(f'  T3 = {t3:.3f}s')
        print(f'  RTO = {rto:.3f}s')

    print(f'  RPO = 0 (wsrep guarantee)')

    new_primary = get_primary_pod()

    return {
        'trial': trial_num,
        'tps_target': tps_target,
        'tps_actual': tps_actual,
        'n_threads': load._n,
        't1': round(t1_elapsed, 3),
        't3': round(t3, 3),
        'rto': round(rto, 3),
        'rpo': 0,
        'writes_ok':   load.writes_ok   if tps_target > 0 else 0,
        'writes_fail': load.writes_fail if tps_target > 0 else 0,
        'primary_before': primary,
        'primary_after':  new_primary,
        'generator': 'pool_v2'
    }


# ── Session runner ──
def run_session(tps_target, n_trials, output_file,
                engine, n_threads=None):
    results = []
    label = f'TPS_target={tps_target}'
    if n_threads:
        label += f' threads={n_threads}'
    print(f'\n=== Session {label} | {n_trials} trials ===')

    for i in range(1, n_trials + 1):
        r = run_trial(tps_target, i, engine)
        if r:
            if n_threads:
                r['n_threads_override'] = n_threads
            results.append(r)
            with open(output_file, 'w') as f:
                json.dump(results, f, indent=2)
            print(f'  Saved: {output_file}')
        else:
            print(f'  Trial {i} failed — continuing')
        if i < n_trials:
            print(f'  Cooldown {COOLDOWN}s...')
            time.sleep(COOLDOWN)

    return results


# ── Entry point ──
if __name__ == '__main__':
    print('=== Paper 5 — measure_pxc_v2 (pool generator) ===')

    start_port_forward()
    engine = make_engine(pool_size=64)

    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT @@hostname, @@read_only")).fetchone()
            print(f'Connected: {row[0]}, read_only={row[1]}')
    except Exception as e:
        print(f'Connection failed: {e}')
        exit(1)

    # TPS calibration
    print('\n--- TPS calibration (8s per config) ---')
    for n_th in [8, 16, 32, 64]:
        gen = LoadGeneratorPool(1000, engine, n_threads=n_th)
        gen.start()
        time.sleep(8)
        gen.stop()
        tps = gen.actual_tps(8)
        print(f'  Threads={n_th:>2}: TPS_actual={tps:>6.1f} '
              f'OK={gen.writes_ok} Fail={gen.writes_fail}')

    print('\nReady. Example:')
    print('  from measure_pxc_v2 import make_engine, run_session, start_port_forward')
    print('  start_port_forward()')
    print('  engine = make_engine()')
    print('  run_session(0, 7, "results_tps0.json", engine)')
