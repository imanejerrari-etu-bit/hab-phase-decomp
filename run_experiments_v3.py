python -c "
import time, json
from measure_pxc_v3 import run_session, start_port_forward, reset_engine

start_port_forward()
reset_engine()

run_session(0,    7, 'v3_tps0.json')
time.sleep(180)
run_session(100,  7, 'v3_tps100.json')
time.sleep(180)
run_session(500,  7, 'v3_tps500.json')
time.sleep(180)
run_session(1000, 7, 'v3_tps1000.json')

print('=== COLLECTE COMPLETE ===')
"