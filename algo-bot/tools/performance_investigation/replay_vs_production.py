"""Does the offline replay predict production on the same opportunities? (execution fidelity)"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

T = {t['opportunity_id']: t for t in trades()}
byid = {}
for line in open(AEQ):
  r = json.loads(line)
  byid.setdefault(r['id'], r)
m = [(T[i], byid[i]) for i in T if i in byid]
print('production trades', len(T), 'found in the replay by opportunity id', len(m))
conf = collections.Counter()
for t, r in m:
  sim = r['sim'].get('oneR_cost', {}).get('result')
  act = 'reached 1R' if t['exit_path'] in ('tp1_be', 'tp2_full', 'tp1_stop') else 'full stop' if t['exit_path'] == 'full_stop' else 'other'
  conf[(sim, act)] += 1
for k, v in sorted(conf.items(), key=lambda x: -x[1]):
  print(' ', k, v)
agree = conf[('target', 'reached 1R')] + conf[('stop', 'full stop')]
decided = sum(v for (s, a), v in conf.items() if s in ('target', 'stop') and a in ('reached 1R', 'full stop'))
print(f'replay and production agree on {agree} of {decided} decided opportunities ({agree / decided:.1%})')
print('fill minus signal time (minutes): median', round(st.median((t['t0'] - r['eval_time']) / 60 for t, r in m), 1))
