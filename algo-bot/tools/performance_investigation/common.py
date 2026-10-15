"""Shared inputs of the performance investigation (docs/strategies/performance-investigation-2026-10.md).

Read-only production exports, produced with ``ssh`` + ``psql`` on the production host (account data, not committed):

* ``inv_trades.csv``: auto_trade_results (trade_stream = 'algo_auto') joined to analysis_opportunities on
  ``opportunity_id = 'opp_' || substring(group_id from 'go_opp_(.*)$')`` with the first fill, the average entry,
  the volume and the opportunity's quality, bias, atr, entry band, invalidation and first target.
* ``inv_env.csv``: the same join with the published stop envelope (floor, cap) and the per-fill stop.
* ``bars/<SYMBOL>.<TF>.jsonl``: ``redis-cli zrange bars:<SYMBOL>:<TF> 0 -1`` (closed bars, the newest 2000).

Closed bars before that come from the committed production captures.
"""
import csv, json, glob, datetime as dt, statistics as st, math, random, collections, os

# Inputs (none of them is committed: they hold account data or are large):
#   INV_DATA   directory with inv_trades.csv, inv_env.csv and bars/<SYMBOL>.<TF>.jsonl (read-only exports, see README in this file's docstring)
#   INV_AEQ    the all-strategy replay records.jsonl (AEQ_DUMP of analysis-engine/test/aequality)
#   INV_REPO   repository root (committed captures live in analysis-engine/testdata)
REPO=os.environ.get('INV_REPO',os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..')))
AEQ=os.environ.get('INV_AEQ','/tmp/aeq/records.jsonl')
PIP={"XAU":0.1,"EURUSD":1e-4,"GBPUSD":1e-4,"USDJPY":0.01,"GBPJPY":0.01}
SPREAD={"XAU":0.25,"EURUSD":0.00008,"GBPUSD":0.0001,"USDJPY":0.008,"GBPJPY":0.02}
TD=os.environ.get('INV_DATA','/tmp/inv')
CAP=os.path.join(REPO,'analysis-engine','testdata')
def bars(sym, tf='M5'):
    d={}
    for p in glob.glob(f'{CAP}/replay-{sym.lower()}-*production-capture-*.json'):
        n=os.path.basename(p)
        if sym=='XAU' and not n.startswith('replay-xau-'): continue
        for r in json.load(open(p))['timeframes'].get(tf,[]): d.setdefault(r[0],(r[0],r[1],r[2],r[3],r[4]))
    for l in open(f'{TD}/bars/{sym}.{tf}.jsonl'):
        if l.strip():
            j=json.loads(l); d[j['t']]=(j['t'],j['o'],j['h'],j['l'],j['c'])
    ts=sorted(d); return [d[t] for t in ts]
REF={'tp1_be':0.53,'tp2_full':1.82,'tp1_stop':1.09,'manual_close':0.0}
def trades():
    out=[]
    for r in csv.DictReader(open(f'{TD}/inv_trades.csv')):
        if r['strategy']=='range_sweep' or not r['avg_entry'] or not r['stop_pips']: continue
        stop=float(r['stop_pips'])
        if r['exit_path']=='full_stop': R=float(r['result_pips'])/stop
        elif r['realized_pips']: R=float(r['realized_pips'])/stop
        else: R=REF.get(r['exit_path'],0.0)
        out.append(dict(r, R=R, stop=stop, entry=float(r['avg_entry']), t0=int(float(r['first_fill'])), t1=int(r['closed_at']), pip=PIP[r['symbol']], sym=r['symbol'], dir=r['direction']))
    return out
