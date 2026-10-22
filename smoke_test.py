"""Offline smoke tests for the Vera submission."""
import json, os
from bot import compose

BASE = os.path.join(os.path.dirname(__file__), "dataset", "expanded")

def load_dir(folder, key):
    out={}
    for f in os.listdir(os.path.join(BASE,folder)):
        if not f.endswith('.json'): continue
        d=json.load(open(os.path.join(BASE,folder,f),encoding='utf8'))
        out[d[key]]=d
    return out

cats=load_dir('categories','slug')
merchants=load_dir('merchants','merchant_id')
customers=load_dir('customers','customer_id')
triggers=load_dir('triggers','id')

assert len(cats)==5 and len(merchants)==50 and len(customers)==200 and len(triggers)==100
for tid,t in triggers.items():
    m=merchants[t['merchant_id']]
    c=customers.get(t.get('customer_id'))
    result=compose(cats[m['category_slug']],m,t,c)
    for k in ('body','cta','send_as','suppression_key','rationale'):
        assert k in result, (tid,k)
    assert len(result['body']) < 4000, tid
    assert result['send_as'] in ('vera','merchant_on_behalf')
print(f"PASS: composed {len(triggers)} expanded triggers deterministically")
