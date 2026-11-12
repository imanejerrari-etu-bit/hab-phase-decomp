import mysql.connector
import subprocess
import threading
import time

# Configuration
HOST = '127.0.0.1'
PORT = 3307
USER = 'moco-admin'
PASSWORD = '17b2b1d5dde14b4d8fb7a0ba5e3a3f51'
DATABASE = 'paper5'
NAMESPACE = 'mysql-operator'
CONTEXT = 'kind-mysql-a'
COOLDOWN = 120

def get_current_primary_pod():
    result = subprocess.run(
        ['kubectl', 'get', 'pods', '-n', NAMESPACE, '--context', CONTEXT,
         '-l', 'moco.cybozu.com/role=primary',
         '-o', 'jsonpath={.items[0].metadata.name}'],
        capture_output=True, text=True, encoding='utf-8'
    )
    name = result.stdout.strip()
    return name if name else None

def start_port_forward(pod_name):
    proc = subprocess.Popen(
        ['kubectl', 'port-forward', f'pod/{pod_name}',
         f'{PORT}:3306', '-n', NAMESPACE, '--context', CONTEXT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    return proc

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
        self.thread = None

    def _worker(self):
        interval = 1.0 / self.tps if self.tps > 0 else 999
        while self.running:
            t0 = time.time()
            try:
                conn = get_conn(timeout=2)
                cursor = conn.cursor()
                cursor.execute('INSERT INTO measurements (val) VALUES (%s)', (1,))
                conn.commit()
                conn.close()
                self.writes_ok += 1
            except:
                self.writes_fail += 1
            elapsed = time.time() - t0
            sleep = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    def start(self):
        self.running = True
        self.writes_ok = 0
        self.writes_fail = 0
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)

def inject_failover(pod_name):
    subprocess.run(
        ['kubectl', 'delete', 'pod', pod_name,
         '-n', NAMESPACE, '--context', CONTEXT,
         '--grace-period=0', '--force'],
        capture_output=True
    )

def wait_for_new_primary(old_pod, t_del, timeout=120):
    """Attend qu'un pod primary soit Ready apres le failover."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        result = subprocess.run(
            ['kubectl', 'get', 'pods', '-n', NAMESPACE, '--context', CONTEXT,
             '-l', 'moco.cybozu.com/role=primary',
             '-o', 'jsonpath={.items[0].metadata.name}={.items[0].status.conditions[?(@.type=="Ready")].status}'],
            capture_output=True, text=True, encoding='utf-8'
        )
        output = result.stdout.strip()
        if '=True' in output:
            pod_name = output.split('=')[0]
            return pod_name, time.time() - t0
        time.sleep(0.5)
    return None, timeout

def wait_for_insert(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if try_insert():
            return time.time() - t0
        time.sleep(0.5)
    return None

def run_trial(tps, trial_num):
    print(f'\n--- Trial {trial_num} (TPS={tps}) ---')

    primary_pod = get_current_primary_pod()
    if not primary_pod:
        print('  ERREUR: impossible de trouver le primary')
        return None
    print(f'  Primary actuel: {primary_pod}')

    pf = start_port_forward(primary_pod)
    print(f'  Port-forward actif vers {primary_pod}:{PORT}')

    try:
        warmup(5)
    except Exception as e:
        print(f'  ERREUR warmup: {e}')
        pf.terminate()
        return None

    n_pre = get_row_count()
    print(f'  N_pre = {n_pre}')

    load = LoadGenerator(tps)
    if tps > 0:
        load.start()
        time.sleep(1)
        print(f'  Charge active: {tps} TPS cible')

    t_del = time.time()
    inject_failover(primary_pod)
    print(f'  Failover injecte (pod={primary_pod})')

    pf.terminate()

    new_primary, t1 = wait_for_new_primary(primary_pod, t_del)
    if new_primary:
        t_elect = t_del + t1
        print(f'  T1 = {t1:.3f}s  (nouveau primary: {new_primary})')
    else:
        print('  T1 = TIMEOUT')
        if tps > 0:
            load.stop()
        return None

    pf2 = start_port_forward(new_primary)
    print(f'  Port-forward vers nouveau primary: {new_primary}')

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
        pf2.terminate()
        return None

    if tps > 0:
        load.stop()

    n_post = get_row_count()
    rpo = (n_pre - n_post) if (n_pre is not None and n_post is not None) else None
    print(f'  RPO = {rpo}' if rpo is not None else '  RPO = ?')
    if tps > 0:
        print(f'  Writes OK={load.writes_ok} Fail={load.writes_fail}')

    pf2.terminate()

    return {
        'trial': trial_num,
        'tps_target': tps,
        'tps_actual': load.writes_ok if tps > 0 else 0,
        't1': round(t1, 3),
        't3': round(t3, 3),
        'rto': round(rto, 3),
        'rpo': rpo,
        'primary_before': primary_pod,
        'primary_after': new_primary
    }

if __name__ == '__main__':
    print('=== Paper 5 - MOCO Load Sensitivity ===')
    primary = get_current_primary_pod()
    print(f'Primary detecte: {primary}')
    pf = start_port_forward(primary)
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT @@hostname, @@read_only')
        row = cursor.fetchone()
        print(f'Connexion OK: {row[0]}, read_only={row[1]}')
        conn.close()
    except Exception as e:
        print(f'Erreur connexion: {e}')
    finally:
        pf.terminate()
    print('Pret. Lance: python -c "from measure_moco import run_trial; print(run_trial(0, 1))"')