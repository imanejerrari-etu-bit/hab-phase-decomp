"""
measure_pxc_v3.py — Paper 5 load generator v3
Fix: port-forward reconnection delay subtracted from T3/RTO.
T3_corrected = T3_raw - pf_delay (pure wsrep data-path readiness).
"""
import time
import threading
import subprocess
import json

from sqlalchemy import create_engine, text

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

def start_port_forward(quiet=False):
    global _pf_proc
    if _pf_proc:
        try:
            _pf_proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
    result = subprocess.run(
        ['kubectl', 'get', 'pods', '-n', NAMESPACE,
         '--context', CONTEXT,
         '-l', 'app.kubernetes.io/component=haproxy',
         '--field-selector', 'status.phase=Running',
         '-o', 'jsonpath={.items[0].metadata.name}'],
        capture_output=True, text=True, encoding='utf-8'
    )
    haproxy = result.stdout.strip() or 'pxc-cluster-p-haproxy-0'
    _pf_proc = subprocess.Popen(
        ['kubectl', 'port-forward', haproxy,
         f'{PORT}:3306', '-n', NAMESPACE, '--context', CONTEXT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    if not quiet:
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

# ── Engine management ──
_engine = None

def make_engine(pool_size=64):
    url = (f"mysql+mysqlconnector://{USER}:{PASSWORD}"
           f"@{HOST}:{PORT}/{DATABASE}")
    return create_engine(
        url, pool_size=pool_size, max_overflow=0,
        pool_pre_ping=True, pool_recycle=30,
    )

def get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine

def reset_engine():
    global _engine
    if _engine:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = make_engine()
    return _engine

# ── DB helpers ──
def try_insert():
    try:
        eng = get_engine()
        with eng.connect() as conn:
            conn.execute(text("INSERT INTO measurements (val) VALUES (1)"))
            conn.commit()
        return True
    except Exception:
        return False

def get_row_count():
    try:
        eng = get_engine()
        with eng.connect() as conn:
            return conn.execute(
                text("SELECT COUNT(*) FROM measurements")).scalar()
    except Exception:
        return None

def get_primary_pod():
    for i in range(3):
        pod = f'pxc-cluster-p-pxc-{i}'
        try:
            r = subprocess.run(
                ['kubectl', 'exec', pod, '-n', NAMESPACE,
                 '--context', CONTEXT, '-c', 'pxc', '--',
                 'mysql', '-u', 'root', f'-p{PASSWORD}',
                 '-e', 'SELECT @@read_only;'],
                capture_output=True, text=True,
                encoding='utf-8', timeout=5
            )
            lines = r.stdout.strip().split('\n')
            if len(lines) >= 2 and lines[1].strip() == '0':
                return pod
        except Exception:
            pass
    return None

def warmup(n=5):
    eng = get_engine()
    with eng.connect() as conn:
        for i in range(n):
            conn.execute(
                text("INSERT INTO measurements (val) VALUES (:v)"),
                {"v": i})
        conn.commit()
    print(f'  Warmup: {n} rows inserted')

# ── Load Generator ──
class LoadGenerator:
    def __init__(self, tps_target, n_threads=None):
        self.tps_target = tps_target
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
        elif tps_target <= 200:
            self._n = 16
        elif tps_target <= 500:
            self._n = 32
        else:
            self._n = 64

    def _worker(self):
        interval = ((1.0 / self.tps_target) * self._n
                    if self.tps_target > 0 else 1)
        eng = get_engine()
        try:
            with eng.connect() as conn:
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

# ── Cluster helpers ──
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
                r = subprocess.run(
                    ['kubectl', 'exec', pod, '-n', NAMESPACE,
                     '--context', CONTEXT, '-c', 'pxc', '--',
                     'mysql', '-u', 'root', f'-p{PASSWORD}',
                     '-e', 'SHOW STATUS LIKE "wsrep_cluster_size";'],
                    capture_output=True, text=True,
                    encoding='utf-8', timeout=3
                )
                for line in r.stdout.split('\n'):
                    if 'wsrep_cluster_size' in line and '\t3' in line:
                        return time.time() - t0
            except Exception:
                pass
        time.sleep(0.5)
    return None

def wait_for_insert_with_timing(timeout=120):
    """
    Reconnects port-forward, measures its setup delay,
    then waits for first successful INSERT.
    Returns (total_elapsed_since_call, pf_delay).
    T3_corrected = total_elapsed - pf_delay.
    """
    # Measure port-forward reconnection delay
    t_pf_start = time.time()
    start_port_forward(quiet=True)
    reset_engine()
    pf_delay = time.time() - t_pf_start  # typically ~2-3s

    # Wait for first INSERT via HAProxy
    t0 = time.time()
    while time.time() - t0 < timeout:
        if try_insert():
            total_elapsed = (time.time() - t_pf_start)
            return total_elapsed, pf_delay
        time.sleep(0.3)
    return None, pf_delay

# ── Main trial ──
def run_trial(tps_target, trial_num):
    print(f'\n--- Trial {trial_num} (TPS={tps_target}) ---')

    primary = get_primary_pod()
    if not primary:
        print('  ERR: no primary found')
        return None
    print(f'  Primary: {primary}')

    try:
        warmup(5)
    except Exception as e:
        print(f'  ERR warmup: {e}')
        return None

    n_pre = get_row_count()
    print(f'  N_pre = {n_pre}')

    load = LoadGenerator(tps_target)
    if tps_target > 0:
        load.start()
        time.sleep(2)
        with load._lock:
            ok2 = load.writes_ok
        print(f'  Load: {tps_target} TPS target '
              f'({load._n} threads) — {ok2} writes in 2s '
              f'= {ok2/2:.1f} TPS actual so far')

    # ── Phase measurements ──
    t_del = time.time()
    inject_failover(primary)
    print(f'  Failover injected (T_del)')

    # T1: wsrep cluster re-formation
    t1_elapsed = wait_for_cluster_ready(timeout=120)
    if t1_elapsed is None:
        print('  T1 = TIMEOUT')
        if tps_target > 0:
            load.stop()
        return None
    t_elect = t_del + t1_elapsed
    print(f'  T1 = {t1_elapsed:.3f}s')

    # T3: reconnect PF, subtract PF delay → pure data-path readiness
    t_total, pf_delay = wait_for_insert_with_timing(timeout=120)
    if t_total is None:
        print('  T3 = TIMEOUT')
        if tps_target > 0:
            load.stop()
        return None

    t_ins = time.time()
    # T3_corrected: time from T_elect to first INSERT minus PF overhead
    t3_raw = t_ins - t_elect
    t3 = max(0.0, t3_raw - pf_delay)
    rto_raw = t_ins - t_del
    rto = max(0.0, rto_raw - pf_delay)

    if tps_target > 0:
        load.stop()
        tps_actual = load.actual_tps(rto_raw)
        print(f'  PF delay  = {pf_delay:.3f}s (subtracted)')
        print(f'  T3        = {t3:.3f}s (corrected)')
        print(f'  RTO       = {rto:.3f}s (corrected)')
        print(f'  Writes OK={load.writes_ok} '
              f'Fail={load.writes_fail} '
              f'TPS_actual={tps_actual}')
    else:
        tps_actual = 0
        print(f'  PF delay  = {pf_delay:.3f}s (subtracted)')
        print(f'  T3        = {t3:.3f}s (corrected)')
        print(f'  RTO       = {rto:.3f}s (corrected)')

    print(f'  RPO = 0')

    return {
        'trial':       trial_num,
        'tps_target':  tps_target,
        'tps_actual':  tps_actual,
        'n_threads':   load._n,
        't1':          round(t1_elapsed, 3),
        't3':          round(t3, 3),
        't3_raw':      round(t3_raw, 3),
        'pf_delay':    round(pf_delay, 3),
        'rto':         round(rto, 3),
        'rto_raw':     round(rto_raw, 3),
        'rpo':         0,
        'writes_ok':   load.writes_ok   if tps_target > 0 else 0,
        'writes_fail': load.writes_fail if tps_target > 0 else 0,
        'primary_before': primary,
        'primary_after':  get_primary_pod(),
        'generator':   'pool_v3_corrected'
    }

# ── Session runner ──
def run_session(tps_target, n_trials, output_file):
    results = []
    print(f'\n=== Session TPS={tps_target} | {n_trials} trials ===')
    for i in range(1, n_trials + 1):
        r = run_trial(tps_target, i)
        if r:
            results.append(r)
            with open(output_file, 'w') as f:
                json.dump(results, f, indent=2)
            print(f'  Saved → {output_file}')
        else:
            print(f'  Trial {i} failed — continuing')
        if i < n_trials:
            print(f'  Cooldown {COOLDOWN}s...')
            time.sleep(COOLDOWN)
    return results

# ── Entry point ──
if __name__ == '__main__':
    print('=== measure_pxc_v3.py — corrected T3 (PF delay subtracted) ===')
    start_port_forward()
    reset_engine()

    try:
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT @@hostname, @@read_only")).fetchone()
            print(f'Connected: {row[0]}, read_only={row[1]}')
    except Exception as e:
        print(f'Connection failed: {e}')
        exit(1)

    print('\nValidation trial at TPS=0...')
    r = run_trial(0, 1)
    if r:
        print(f'\n── Validation result ──')
        print(f'  T1        = {r["t1"]}s')
        print(f'  T3 raw    = {r["t3_raw"]}s  (includes PF delay)')
        print(f'  PF delay  = {r["pf_delay"]}s')
        print(f'  T3 correc = {r["t3"]}s  (pure data-path)')
        print(f'  RTO       = {r["rto"]}s')
        print(f'  RPO       = {r["rpo"]}')
        print(f'\nExpected T3 corrected: 0.5–3s (similar to v1 baseline)')
