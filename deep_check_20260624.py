import csv, os, re
from collections import defaultdict, Counter
from pathlib import Path

DIR = Path('/Users/taopeng/PycharmProjects/stock_fund_flow/research_data/backtest/1430_entry/20260624/enriched')
rows = []
for fp in sorted(DIR.glob('snapshot_*_enriched.csv')):
    m = re.match(r'snapshot_(\d{6})_enriched\.csv', fp.name)
    ts = m.group(1) if m else '?'
    with open(fp, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            row['_snapshot'] = ts
            row['_pnl'] = float(row['收益%'].strip())
            row['_siglist'] = [s.strip() for s in row['综合信号'].split(',') if s.strip()]
            row['_startlist'] = [s.strip() for s in row['启动信号'].split(',') if s.strip()]
            rows.append(row)

# Start signal analysis
print('=== Start Signals (N>=1000) ===')
ss = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
for r in rows:
    for t in r['_startlist']:
        s = ss[t]; s['n']+=1; s['pnl']+=r['_pnl']
        if r['胜负']=='胜': s['w']+=1
for t,s in sorted(ss.items(), key=lambda x:-x[1]['n']):
    if s['n']>=1000:
        print(f'  {t:30s} N={s["n"]:>7d}  avg={s["pnl"]/s["n"]:+.2f}%  wr={s["w"]/s["n"]*100:.1f}%')

# Verify if 100% combos are concentrated in few snapshots
print('\n=== Combo snapshot distribution (Top 5 combos) ===')
combos = [
    sorted(['P32_extreme']),
    sorted(['P32_extreme','P35_short_cover','P37_momentum_down']),
    sorted(['P32_ratio_accel','P_low_vol_ratio']),
    sorted(['P32_extreme','P34_gap_standalone','P34_gap_strong','P_low_liquidity','P_low_vol_ratio']),
    sorted(['P32_ratio_accel','P37_momentum_up','P_high_price']),
]
for target in combos:
    name = ','.join(target)
    snaps = Counter()
    results = Counter()
    for r in rows:
        if sorted(r['_siglist']) == target:
            snaps[r['_snapshot']] += 1
            results[r['胜负']] += 1
    if snaps:
        top_snaps = snaps.most_common(3)
        print(f'  {name}: N={sum(snaps.values())} results={dict(results)} top_snaps={top_snaps}')

# P34_P32_combo detail
print('\n=== P34_P32_combo items (first 5) ===')
for r in rows:
    if 'P34_P32_combo' in r['_siglist']:
        print(f'  {r["代码"]} {r["名称"]} {r["行业"]} sigs={r["_siglist"]} pnl={r["_pnl"]:+.2f}% {r["胜负"]}')
    if sum(1 for x in rows if 'P34_P32_combo' in x['_siglist'] and x['代码']==r['代码'] and x['_snapshot']<=r['_snapshot']) >= 5:
        break

# Industry best
print('\n=== Industry Top (N>=200) ===')
ind = defaultdict(lambda: {'n':0,'pnl':0,'w':0})
for r in rows:
    n = r['行业'].strip()
    s = ind[n]; s['n']+=1; s['pnl']+=r['_pnl']
    if r['胜负']=='胜': s['w']+=1
for n,s in sorted(ind.items(), key=lambda x:-(x[1]['w']/x[1]['n']))[:15]:
    if s['n']>=200:
        print(f'  {n:20s} N={s["n"]:>6d}  avg={s["pnl"]/s["n"]:+.2f}%  wr={s["w"]/s["n"]*100:.1f}%')

print('\nDone.')
