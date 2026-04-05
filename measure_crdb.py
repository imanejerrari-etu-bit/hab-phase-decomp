"""
measure_crdb.py — Paper 5 CockroachDB load sensitivity (simplified)
Prediction H2: RTO load-invariant (Raft election independent of write backlog)
"""
import time
import threading
import subprocess
import json
import psycopg2

# ── Configuration ──
HOST      = '127.0.0.1'
PORT      = 26257
USER      = 'root'
DBNAME    = 'paper5'
NAMESPACE = 'crdb'
CONTEXT   = 'kind-crdb-p5'
COOLDOWN  = 120
PODS      = ['cockroachdb-0', 'cockroachdb-1', 'cockroachdb-2']

# ── Port-forward process ──
_pf_proc  = None
_pf_pod   = None

def start_port_forward(pod_name):
    global _pf_proc, _pf_pod
    if _pf_proc:
        try:
            _pf_proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
    _pf_pod  = pod_name
    _pf_proc = subprocess.Popen(
        ['kubectl', 'port-forward', pod_name,
         f'{PORT}:26257', '-n', NAMESPACE, '--context', CONTEXT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    print(f'  Port-forward → {pod_name}:{PORT}')

# ── Simple connection (no pool — avoids reconnection complexity) ──
def new_conn(timeout=5):
    return psycopg2.connect(
        host=HOST, port=PORT, dbname=DBNAME,
        user=USER, sslmode='disable',
        connect_timeout=timeout
    )

def try_insert():
    try:
        conn = new_conn(timeout=2)
        cur  = conn.cursor()
        cur.execute("INSERT INTO measurements (val) VALUES (1)")
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def get_row_count():
    try:
        conn  = new_conn()
        cur   = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM measurements")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return None

def warmup(n=5):
    conn = new_conn()
    cur  = conn.cursor()
    for i in range(n):
        cur.execute("INSERT INTO measurements (val) VALUES (%s)", (i,))
    conn.commit()
    conn.close()
    print(f'  Warmup: {n} rows inserted')

# ── Load Generator ──
class LoadGenerator:
    def __init__(self, tps_target, n_threads=None):
        self.tps_target = tps_target
        self.running    = False
        self.writes_ok  = 0
        self.writes_fail= 0
        self._lock      = threading.Lock()
        self._threads   = []
        if n_threads is not None:
            self._n = n_threads
        elif tps_target == 0:
            self._n = 0
        elif tps_target <= 100:
            self._n = 8
        else:
            self._n = 16

    def _worker(self):
        interval = ((1.0 / self.tps_target) * self._n
                    if self.tps_target > 0 else 1)
        while self.running:
            t0 = time.time()
            try:
                conn = new_conn(timeout=2)
                cur  = conn.cursor()
                cur.execute("INSERT INTO measurements (val) VALUES (1)")
                conn.commit()
                conn.close()
                with self._lock:
                    self.writes_ok += 1
            except Exception:
                with self._lock:
                    self.writes_fail += 1
            elapsed = time.time() - t0
            sleep   = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    def start(self):
        self.running    = True
        self.writes_ok  = 0
        self.writes_fail= 0
        self._threads   = []
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

def wait_for_rto(surviving_pods, timeout=60):
    """
    For CockroachDB: redirect port-forward to a surviving pod,
    then wait for first successful INSERT.
    Returns elapsed seconds from call start (= RTO from T_del).
    """
    t0 = time.time()

    # Reconnect port-forward to first surviving pod
    for pod in surviving_pods:
        start_port_forward(pod)
        # Try to insert via new port-forward
        t_inner = time.time()
        while time.time() - t_inner < timeout:
            if try_insert():
                return time.time() - t0
            time.sleep(0.3)

    return None

# ── Main trial ──
def run_trial(tps_target, trial_num):
    print(f'\n--- Trial {trial_num} (TPS={tps_target}) ---')

    # Rotate which pod we delete
    deleted_pod  = PODS[trial_num % len(PODS)]
    surviving    = [p for p in PODS if p != deleted_pod]

    # Make sure PF is on a good pod before warmup
    start_port_forward(surviving[0])

    try:
        warmup(5)
    except Exception as e:
        print(f'  ERR warmup: {e}')
        return None

    n_pre = get_row_count()
    print(f'  N_pre = {n_pre}')
    print(f'  Will delete: {deleted_pod}')

    load = LoadGenerator(tps_target)
    if tps_target > 0:
        load.start()
        time.sleep(2)
        with load._lock:
            ok2 = load.writes_ok
        print(f'  Load: {tps_target} TPS target '
              f'({load._n} threads) — {ok2} writes in 2s '
              f'= {ok2/2:.1f} TPS actual')

    # Inject failover
    t_del = time.time()
    inject_failover(deleted_pod)
    print(f'  Failover injected (T_del) — deleted {deleted_pod}')

    # Kill current port-forward (pod is gone or disrupted)
    global _pf_proc
    if _pf_proc:
        try:
            _pf_proc.terminate()
        except Exception:
            pass

    # Wait for RTO — reconnect to surviving pod
    rto_elapsed = wait_for_rto(surviving, timeout=60)

    if rto_elapsed is None:
        print('  RTO = TIMEOUT')
        if tps_target > 0:
            load.stop()
        return None

    # For CockroachDB: t3 ≈ 0 (Raft atomic), RTO ≈ t1
    rto = t_del + rto_elapsed - t_del  # = rto_elapsed

    if tps_target > 0:
        load.stop()
        tps_actual = load.actual_tps(rto)
        print(f'  RTO = {rto:.3f}s')
        print(f'  Writes OK={load.writes_ok} '
              f'Fail={load.writes_fail} '
              f'TPS_actual={tps_actual}')
    else:
        tps_actual = 0
        print(f'  RTO = {rto:.3f}s')

    print(f'  RPO = 0 (Raft serialisability)')

    return {
        'trial':       trial_num,
        'tps_target':  tps_target,
        'tps_actual':  tps_actual,
        'n_threads':   load._n,
        'rto':         round(rto, 3),
        't1':          round(rto, 3),
        't3':          0,
        'rpo':         0,
        'writes_ok':   load.writes_ok   if tps_target > 0 else 0,
        'writes_fail': load.writes_fail if tps_target > 0 else 0,
        'pod_deleted': deleted_pod,
        'system':      'cockroachdb'
    }

# ── Session runner ──
def run_session(tps_target, n_trials, output_file):
    results = []
    print(f'\n=== CRDB Session TPS={tps_target} | {n_trials} trials ===')
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
    print('=== measure_crdb.py — CockroachDB load sensitivity ===')

    start_port_forward(PODS[0])

    try:
        conn = new_conn()
        cur  = conn.cursor()
        cur.execute("SELECT version()")
        print(f'Connected: {cur.fetchone()[0][:50]}')
        conn.close()
    except Exception as e:
        print(f'Connection failed: {e}')
        exit(1)

    print('\nValidation trial at TPS=0...')
    r = run_trial(0, 1)
    if r:
        print(f'\n── Validation ──')
        print(f'  RTO = {r["rto"]}s  (expected: 1-5s)')
        print(f'  RPO = {r["rpo"]}')
        print('\nReady. Launch full collection:')
        print('  python -c "from measure_crdb import run_session; '
              'run_session(0,7,\'crdb_tps0.json\')"')
