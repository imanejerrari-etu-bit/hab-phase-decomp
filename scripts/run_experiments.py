import time
import json
from measure_pxc import run_trial

COOLDOWN = 120

def run_session(tps, n_trials, output_file):
    results = []
    print(f'\n=== Session TPS={tps} | {n_trials} trials ===')
    for i in range(1, n_trials + 1):
        result = run_trial(tps, i)
        if result:
            results.append(result)
            with open(output_file, 'w') as f:
                json.dump(results, f, indent=2)
            print(f'  Sauvegarde: {output_file}')
        else:
            print(f'  Trial {i} echoue — on continue')
        if i < n_trials:
            print(f'  Cooldown {COOLDOWN}s...')
            time.sleep(COOLDOWN)
    return results

if __name__ == '__main__':
    # Session 0 TPS — baseline
    run_session(tps=0, n_trials=7, output_file='results_pxc_tps0_clean.json')
    print('\n=== Baseline complete ===')
    
    print('\nCooldown 3 min avant session 100 TPS...')
    time.sleep(180)
    
    # Session 100 TPS
    run_session(tps=100, n_trials=7, output_file='results_pxc_tps100_clean.json')
    print('\n=== 100 TPS complete ===')
    
    print('\nCooldown 3 min avant session 500 TPS...')
    time.sleep(180)
    
    # Session 500 TPS
    run_session(tps=500, n_trials=7, output_file='results_pxc_tps500_clean.json')
    print('\n=== 500 TPS complete ===')
    
    print('\nCooldown 3 min avant session 1000 TPS...')
    time.sleep(180)
    
    # Session 1000 TPS
    run_session(tps=1000, n_trials=7, output_file='results_pxc_tps1000_clean.json')
    print('\n=== 1000 TPS complete ===')
    
    print('\n=== COLLECTE COMPLETE ===')