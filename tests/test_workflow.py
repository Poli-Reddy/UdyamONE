import json
import secrets

from fastapi.testclient import TestClient
import app as mod

client = TestClient(mod.app)


def new_order(needs=None):
    needs = needs or ["fibre", "cctv", "email"]
    profile = dict(tier=1, employees=12, sites=1, gstin=True, type="clinic", needs=needs, context=dict(locations=[dict(id="main", name="Main", employees=12, needs=needs)], inventory_confirmed=True))
    rec = client.post("/api/recommend", json=profile).json()
    assert rec["plan"]["ready"], rec
    body = dict(business_name="Test Clinic", contact_name="Asha", phone="9876543210", address="1 Main Road, Delhi", profile=profile, consent=True, partner_code="REP-1", plan_fingerprint=rec["plan"]["fingerprint"])
    r = client.post("/api/orders", json=body)
    assert r.status_code == 200, r.text
    return r.json(), body


def event(order, task, action="complete", **kw):
    t = next(t for t in order["activation"]["tasks"] if t["id"] == task)
    payload = dict(event_id=secrets.token_hex(10), task_id=task, action=action, expected_version=t["version"], evidence="Synthetic test evidence", **kw)
    return client.post(f"/api/orders/{order['id']}/events", json=payload), payload


def test_stale_acceptance_and_cross_order_access():
    o, body = new_order()
    body["profile"]["context"]["budget"] = 100000
    assert client.post("/api/orders", json=body).status_code == 409
    assert client.get('/api/orders/'+o['id']).status_code == 401
    assert client.get('/api/orders/'+o['id'], headers={'x-order-token':'wrong'}).status_code == 401
    assert client.get('/api/orders/'+o['id'], headers={'x-order-token':o['access_token']}).status_code == 200
    assert o['partner_code'] == 'REP-1'


def test_task_prerequisites_delays_failures_and_independent_progress():
    o, _ = new_order()
    assert event(o, "main:P23:check")[0].status_code == 409
    assert event(o, "verification")[0].status_code == 409
    o = client.post('/api/orders/'+o['id']+'/demo-verification').json()
    old = {t['id']: t['forecast'] for t in o['activation']['tasks']}
    r, _ = event(o, 'main:P01:feasibility', 'delay', delay_days=9)
    assert r.status_code == 200, r.text
    o = r.json()
    now = {t['id']: t['forecast'] for t in o['activation']['tasks']}
    assert now['main:P23:check'] > old['main:P23:check']
    assert now['main:P16:check'] == old['main:P16:check']
    r, _ = event(o, 'main:P01:feasibility', 'retry')
    assert r.status_code == 200
    o = r.json()
    assert next(t for t in o['activation']['tasks'] if t['id'] == 'main:P01:feasibility')['status'] == 'pending'
    r, _ = event(o, 'main:P01:feasibility', 'fail')
    o = r.json()
    assert o['activation']['forecast'] is None and o['activation']['alternatives']
    r, _ = event(o, 'main:P16:provision')
    assert r.status_code == 200
    r, _ = event(r.json(), 'main:P16:check')
    assert r.json()['activation']['completed_services'] == 1
    assert r.json()['monthly'] == o['monthly']


def test_idempotent_event_and_conflicting_replay():
    o, _ = new_order(['email'])
    o = client.post('/api/orders/'+o['id']+'/demo-verification').json()
    r, body = event(o, 'main:P16:provision')
    assert r.status_code == 200
    again = client.post('/api/orders/'+o['id']+'/events',json=body)
    assert again.status_code == 200
    assert len(again.json()['activation']['events']) == 2
    body['evidence'] = 'Different evidence with reused ID'
    assert client.post('/api/orders/'+o['id']+'/events',json=body).status_code == 409
    assert client.post('/api/orders/'+o['id']+'/status',json={'status':'activated'}).status_code == 409
    r, _ = event(r.json(), 'main:P16:check')
    assert r.json()['status'] == 'activated'


def test_admin_required_outside_demo_and_simulation_disabled(monkeypatch):
    o, _ = new_order()
    monkeypatch.setenv('UDYAM_DEMO','false')
    assert client.post('/api/orders/'+o['id']+'/demo-verification').status_code == 503
    monkeypatch.setattr(mod,'ADMIN_TOKEN','operator-secret')
    assert client.get('/api/orders').status_code == 401
    assert client.post('/api/orders/'+o['id']+'/demo-verification',headers={'x-admin-token':'operator-secret'}).status_code == 403


def test_kyc_lane_and_order_identity_binding(monkeypatch):
    import kyc
    o, _ = new_order()
    headers = {'x-order-token':o['access_token']}
    r = client.post('/api/kyc',data={'consent':'true','lane':'gst_free','order_id':o['id']},headers=headers)
    assert r.status_code == 409
    monkeypatch.setattr(kyc,'check',lambda *a,**kw: ('U','VERIFIED',[]))
    monkeypatch.setattr(kyc,'bind_order',lambda *a,**kw: [dict(sev='action',code='order_business_mismatch',msg='Mismatch',fix='Review')])
    r = client.post('/api/kyc',data={'consent':'true','lane':'standard','order_id':o['id']},headers=headers)
    assert r.json()['verdict'] == 'ACTION_REQUIRED'
    got = client.get('/api/orders/'+o['id'],headers=headers).json()
    assert got['activation']['tasks'][0]['status'] != 'complete'


def test_pilot_assigns_once_and_rejects_premature_retention():
    o, _ = new_order()
    a = client.post('/api/assistance/'+o['id']+'/assign').json()
    b = client.post('/api/assistance/'+o['id']+'/assign').json()
    assert a['assignment'] == b['assignment']
    assert not a['dispatched']
    assert client.post('/api/assistance/'+o['id']+'/outcome',json={'cost':250,'outcome':'retained_90d'}).status_code == 409


def test_recovery_requires_new_acceptance_and_preserves_independent_work():
    o,_ = new_order()
    h = {'x-order-token':o['access_token']}
    o = client.post('/api/orders/'+o['id']+'/demo-verification').json()
    r,_ = event(o,'main:P01:feasibility','fail');o=r.json()
    r,_ = event(o,'main:P16:provision');o=r.json()
    r,_ = event(o,'main:P16:check');o=r.json()
    path='/api/orders/'+o['id']+'/recovery'
    wired=client.post(path,json={},headers=h).json()
    assert not wired['plan']['ready']
    draft=client.post(path,json={'allow_wireless':True},headers=h).json()
    assert draft['plan']['ready'] and not draft['applied']
    assert 'P04' in [i['id'] for i in draft['plan']['quote']['items']]
    before=client.get('/api/orders/'+o['id'],headers=h).json()
    assert before['monthly']==o['monthly']
    body=dict(allow_wireless=True,consent=True,base_fingerprint=draft['base_fingerprint'],candidate_fingerprint=draft['plan']['fingerprint'])
    assert client.post(path+'/accept',json={**body,'consent':False},headers=h).status_code==400
    accepted=client.post(path+'/accept',json=body,headers=h)
    assert accepted.status_code==200,accepted.text
    o=accepted.json()
    assert o['monthly']==draft['plan']['quote']['monthly']
    by={t['id']:t for t in o['activation']['tasks']}
    assert by['main:P16:check']['status']=='complete'
    assert 'main:P04:check' in by['main:P23:provision']['dependencies']
    assert by['main:P04:feasibility']['status']=='pending'
    assert by['main:P23:provision']['version'] == 1
    stale=dict(event_id=secrets.token_hex(12),task_id='main:P23:provision',action='delay',expected_version=0,evidence='Old task plan event',delay_days=3)
    assert client.post('/api/orders/'+o['id']+'/events',json=stale).status_code==409
    with mod._conn() as c:
        assert c.execute('SELECT COUNT(*) FROM plan_revisions WHERE order_id=?',(o['id'],)).fetchone()[0]==1


def test_wireless_recovery_never_bypasses_failover_dependency():
    o,_=new_order(['fibre','failover'])
    h={'x-order-token':o['access_token']}
    r,_=event(o,'main:P01:feasibility','fail');o=r.json()
    draft=client.post('/api/orders/'+o['id']+'/recovery',json={'allow_wireless':True},headers=h).json()
    assert not draft['plan']['ready']


def test_paid_or_pending_payment_requires_billing_review_before_recovery():
    o,_=new_order()
    h={'x-order-token':o['access_token']}
    r,_=event(o,'main:P01:feasibility','fail');o=r.json()
    path='/api/orders/'+o['id']+'/recovery'
    d=client.post(path,json={'allow_wireless':True},headers=h).json()
    with mod._conn() as c:
        c.execute("INSERT INTO payment_intents VALUES (?,?,?,?,NULL,NULL,'creating','test')",(o['id'],secrets.token_hex(8),o['monthly']*100,'INR'))
    body=dict(allow_wireless=True,consent=True,base_fingerprint=d['base_fingerprint'],candidate_fingerprint=d['plan']['fingerprint'])
    assert client.post(path+'/accept',json=body,headers=h).status_code==409
    assert client.get('/api/orders/'+o['id'],headers=h).json()['monthly']==o['monthly']


def test_kyc_binding_checks_extracted_business_contact_and_address(tmp_path,monkeypatch):
    import kyc
    docs={'gst_certificate':['Legal name: Test Clinic','Trade name: Test Clinic'],'udyam_certificate':['Enterprise name: Test Clinic'],'signatory_id':['Name: Asha','For business: Test Clinic'],'address_proof':['Supply address: 1 Main Road, Delhi']}
    for name in docs:
        (tmp_path/('U_'+name+'.png')).write_bytes(b'fixture')
    monkeypatch.setattr(kyc,'ocr',lambda path:(docs[str(path).split('U_')[-1].removesuffix('.png')],500,0))
    order=dict(business_name='Test Clinic',contact_name='Asha',address='1 Main Road, Delhi')
    assert kyc.bind_order(str(tmp_path),'U',order)==[]
    errors=kyc.bind_order(str(tmp_path),'U',{**order,'business_name':'Different Clinic','contact_name':'Raj','address':'99 Other Street, Mumbai'})
    assert {e['code'] for e in errors}=={'order_business_mismatch','order_signatory_mismatch','order_address_mismatch'}
