"""Export actual API response shapes for JS presentation contract tests."""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='udyam-ui-fixtures-') as folder:
    os.environ['UDYAM_DB']=str(Path(folder)/'test.db')
    os.environ['UDYAM_CONSENT_LOG']=str(Path(folder)/'consent.jsonl')
    os.environ['UDYAM_DEMO']='true'
    from fastapi.testclient import TestClient
    import app
    client=TestClient(app.app)
    profile=dict(tier=1,employees=12,sites=1,gstin=True,type='clinic',needs=['fibre','cctv','email'],context=dict(locations=[dict(id='main',name='Main clinic',employees=12,needs=['fibre','cctv','email'])],inventory_confirmed=True))
    rec=client.post('/api/recommend',json=profile).json()
    result=client.post('/api/orders',json=dict(business_name='Demo Clinic',contact_name='Asha',phone='9876543210',address='1 Demo Street, Delhi',profile=profile,consent=True,plan_fingerprint=rec['plan']['fingerprint']))
    assert result.status_code==200,result.text
    order=result.json();order.pop('access_token')
    profile['context']['locations'][0]['needs']=['email']
    profile['context']['owned']=[dict(site_id='main',capability='email',quantity=5,adequacy='adequate',monthly=700)]
    empty=client.post('/api/recommend',json=profile).json()
    print(json.dumps(dict(order=order,recommendation=rec,nothing_to_buy=empty)))
