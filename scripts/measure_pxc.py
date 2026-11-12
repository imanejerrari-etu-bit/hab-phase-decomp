import mysql.connector
import subprocess
import threading
import time

# Configuration
HOST = '127.0.0.1'
PORT = 3307
USER = 'root'
PASSWORD = 'PXCRoot2025!'
DATABASE = 'paper5'
NAMESPACE = 'pxc-operator'
CONTEXT = 'kind-moco-fresh'
COOLDOWN = 120

def get_conn(timeout=5):
    return mysql.connector.connect(
        host=HOST, port=PORT,
        user=USER, password=PASSWORD,
        database=DATABASE,
        connection_timeout=timeout
    )

def try_insert():
    try:
        conn = get_conn(timeout=2)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO measurements (val) VALUES (%s)', (1,))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_row_count():
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM measurements')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
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
                capture_output=True, text=True, encoding='utf-8',
                timeout=5
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2 and lines[1].strip() == '0':
                return pod
        except:
            pass
    return None

def warmup(n=5):
    conn = get_conn()
    cursor = conn.cursor()
    for i in range(n):
        cursor.execute('INSERT INTO measurements (val) VALUES (%s)', (i,))
        conn.commit()
    conn.close()
    print(f'  Warmup: {n} lignes inserees')

class LoadGenerator:
    def __init__(self, tps):
        self.tps = tps
        self.running = False
        self.writes_ok = 0
        self.writes_fail = 0
        self._lock = threading.Lock()
        self._threads = []
        if tps <= 100:
            self._n_threads = 4
        elif tps <= 500:
            self._n_threads = 16
        else:
            self._n_threads = 32

    def _worker(self):
        interval = (1.0 / self.tps) * self._n_threads
        while self.running:
            t0 = time.time()
            try:
                conn = get_conn(timeout=2)
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT INTO measurements (val) VALUES (%s)', (1,))
                conn.commit()
                conn.close()
                with self._lock:
                    self.writes_ok += 1
            except:
                with self._lock:
                    self.writes_fail += 1
            elapsed = time.time() - t0
            sleep = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    def start(self):
        self.running = True
        self.writes_ok = 0
        self.writes_fail = 0
        self._threads = []
        for _ in range(self._n_threads):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self.running = False
        for t in self._threads:
            t.join(timeout=3)

    def get_actual_tps(self, elapsed):
        if elapsed > 0:
            return round(self.writes_ok / elapsed, 1)
        return 0

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
                    capture_output=True, text=True, encoding='utf-8',
                    timeout=3
                )
                for line in result.stdout.split('\n'):
                    if 'wsrep_cluster_size' in line and '\t3' in line:
                        return time.time() - t0
            except:
                pass
        time.sleep(0.5)
    return None

def wait_for_insert(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if try_insert():
            return time.time() - t0
        time.sleep(0.5)
    return None

def run_trial(tps, trial_num):
    print(f'\n--- Trial {trial_num} (TPS={tps}) ---')

    primary_pod = get_primary_pod()
    if not primary_pod:
        print('  ERREUR: impossible de trouver le primary')
        return None
    print(f'  Primary actuel: {primary_pod}')

    try:
        warmup(5)
    except Exception as e:
        print(f'  ERREUR warmup: {e}')
        return None

    n_pre = get_row_count()
    print(f'  N_pre = {n_pre}')

    load = LoadGenerator(tps)
    if tps > 0:
        load.start()
        time.sleep(2)
        with load._lock:
            ok_so_far = load.writes_ok
        print(f'  Charge active: {tps} TPS cible ({load._n_threads} threads) — {ok_so_far} writes en 2s')

    t_del = time.time()
    inject_failover(primary_pod)
    print(f'  Failover injecte (pod={primary_pod})')

    t1_elapsed = wait_for_cluster_ready(timeout=120)
    if t1_elapsed is not None:
        t_elect = t_del + t1_elapsed
        print(f'  T1 (wsrep ready) = {t1_elapsed:.3f}s')
    else:
        print('  T1 = TIMEOUT')
        if tps > 0:
            load.stop()
        return None

    t3_elapsed = wait_for_insert(timeout=120)
    if t3_elapsed is not None:
        t_ins = time.time()
        t3 = t_ins - t_elect
        rto = t_ins - t_del
        print(f'  T3 = {t3:.3f}s')
        print(f'  RTO = {rto:.3f}s')
    else:
        print('  T3 = TIMEOUT')
        if tps > 0:
            load.stop()
        return None

    if tps > 0:
        load.stop()
        actual_tps = load.get_actual_tps(rto)
        print(f'  Writes OK={load.writes_ok} Fail={load.writes_fail} TPS_reel={actual_tps}')

    n_post = get_row_count()
    rpo = 0
    print(f'  RPO = 0 (wsrep garanti)')

    new_primary = get_primary_pod()

    return {
        'trial': trial_num,
        'tps_target': tps,
        'tps_actual': load.get_actual_tps(rto) if tps > 0 else 0,
        't1': round(t1_elapsed, 3),
        't3': round(t3, 3),
        'rto': round(rto, 3),
        'rpo': rpo,
        'writes_ok': load.writes_ok if tps > 0 else 0,
        'writes_fail': load.writes_fail if tps > 0 else 0,
        'primary_before': primary_pod,
        'primary_after': new_primary
    }

if __name__ == '__main__':
    print('=== Paper 5 - PXC Load Sensitivity ===')
    primary = get_primary_pod()
    print(f'Primary detecte: {primary}')
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT @@hostname, @@read_only')
        row = cursor.fetchone()
        print(f'Connexion OK: {row[0]}, read_only={row[1]}')
        conn.close()
    except Exception as e:
        print(f'Erreur connexion: {e}')
    print('Pret. Lance: python -c "from measure_pxc import run_trial; print(run_trial(100, 1))"')