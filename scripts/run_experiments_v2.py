import time, json
from measure_pxc_v2 import make_engine, run_session, start_port_forward

start_port_forward()
engine = make_engine(pool_size=64)

# Baseline
run_session(0,   7, 'v2_tps0.json',   engine)
time.sleep(180)

# ~43 TPS (8 threads)
run_session(100, 7, 'v2_tps43.json',  engine)
time.sleep(180)

# ~60 TPS (16 threads)
run_session(500, 7, 'v2_tps60.json',  engine)
time.sleep(180)

# ~69 TPS (32 threads)
run_session(1000, 7, 'v2_tps69.json', engine)

print('\n=== COLLECTE COMPLETE ===')