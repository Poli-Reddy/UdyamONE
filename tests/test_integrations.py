import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as mod
import providers
from whatsapp_bot import respond
from test_workflow import new_order

client = TestClient(mod.app)


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv('RAZORPAY_KEY_ID','rzp_test_fixture')
    monkeypatch.setenv('RAZORPAY_KEY_SECRET','fixture-key')
    monkeypatch.setenv('RAZORPAY_WEBHOOK_SECRET','fixture-webhook')
    monkeypatch.setenv('PAYMENT_MODE','test')


def signed(path, body, secret, header, prefix='', **headers):
    raw = json.dumps(body).encode()
    signature = prefix+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    return client.post(path,content=raw,headers={header:signature,'Content-Type':'application/json',**headers})


def test_missing_providers_fail_closed(monkeypatch):
    for key in ('RAZORPAY_KEY_ID','CASHFREE_CLIENT_ID','WHATSAPP_APP_SECRET'):
        monkeypatch.delenv(key,raising=False)
    o,_ = new_order(['email'])
    h={'x-order-token':o['access_token']}
    assert client.post('/api/orders/'+o['id']+'/payment-link',headers=h).status_code == 503
    assert client.post('/api/orders/'+o['id']+'/registry',json={'kind':'udyam','number':'UDYAM-UP-43-1234567','consent':True},headers=h).status_code == 503
    assert providers.status()['telecom']['status'] == 'not_connected'


def test_payment_link_amount_idempotency_webhook_and_reconciliation(gateway,monkeypatch):
    calls=[]
    async def request(method,url,**kw):
        calls.append((method,url,kw))
        body=kw['json']
        return {**body,'id':'plink_fixture','short_url':'https://rzp.io/rzp/fixture'}
    monkeypatch.setattr(providers,'request',request)
    o,_=new_order(['email'])
    h={'x-order-token':o['access_token']}
    path='/api/orders/'+o['id']+'/payment-link'
    r=client.post(path,headers=h)
    assert r.status_code == 200,r.text
    link=r.json()
    assert link['amount']==(o['monthly']+o['setup'])*100
    assert client.post(path,headers=h).json()['reference']==link['reference']
    assert len(calls)==1
    assert not calls[0][2]['json']['notify']['sms']
    entity=dict(id='plink_fixture',reference_id=link['reference'],amount=link['amount'],amount_paid=link['amount'],currency='INR',status='paid',order_id='order_fixture')
    pay=dict(amount=link['amount'],currency='INR',captured=True,status='captured',order_id='order_fixture')
    body=dict(event='payment_link.paid',payload=dict(payment_link=dict(entity=entity),payment=dict(entity=pay)))
    assert client.post('/api/webhooks/razorpay',json=body).status_code==401
    bad=json.loads(json.dumps(body));bad['payload']['payment']['entity']['amount']+=1
    assert signed('/api/webhooks/razorpay',bad,'fixture-webhook','x-razorpay-signature').status_code==409
    assert signed('/api/webhooks/razorpay',body,'fixture-webhook','x-razorpay-signature').status_code==200
    assert signed('/api/webhooks/razorpay',body,'fixture-webhook','x-razorpay-signature').json()['duplicate']
    assert client.get('/api/orders/'+o['id']+'/payment',headers=h).json()['status']=='paid'
    late=dict(event='payment_link.expired',payload=dict(payment_link=dict(entity={**entity,'status':'expired'})))
    assert signed('/api/webhooks/razorpay',late,'fixture-webhook','x-razorpay-signature').status_code==200
    assert client.get('/api/orders/'+o['id']+'/payment',headers=h).json()['status']=='paid'
    assert client.get('/api/orders/'+o['id'],headers=h).json()['status']=='quote_accepted'


def test_uncertain_payment_does_not_create_duplicate_link(gateway,monkeypatch):
    count=[]
    async def fail(*a,**kw):
        count.append(1)
        raise HTTPException(502,'Provider timeout')
    monkeypatch.setattr(providers,'payment_link',fail)
    o,_=new_order(['email'])
    h={'x-order-token':o['access_token']}
    assert client.post('/api/orders/'+o['id']+'/payment-link',headers=h).status_code==502
    assert client.post('/api/orders/'+o['id']+'/payment-link',headers=h).status_code==409
    assert len(count)==1
    assert client.get('/api/orders/'+o['id']+'/payment',headers=h).json()['status']=='needs_reconciliation'


def test_live_payment_keys_cannot_charge_fictional_catalogue(gateway,monkeypatch):
    monkeypatch.setenv('RAZORPAY_KEY_ID','rzp_live_fixture')
    with pytest.raises(HTTPException) as caught:
        providers.payment_config()
    assert caught.value.status_code==503


def test_registry_identity_scope_minimisation_and_failure(monkeypatch):
    monkeypatch.setenv('CASHFREE_CLIENT_ID','fixture')
    monkeypatch.setenv('CASHFREE_CLIENT_SECRET','fixture')
    monkeypatch.setenv('CASHFREE_MODE','sandbox')
    calls=[]
    async def request(method,url,**kw):
        calls.append((url,kw))
        return dict(status='SUCCESS',udyam_number='UDYAM-UP-43-1234567',enterprise_name='Test Clinic',reference_id='ref1',social_category='DO NOT RETAIN')
    monkeypatch.setattr(providers,'request',request)
    o,_=new_order(['email']);h={'x-order-token':o['access_token']}
    body=dict(kind='udyam',number='UDYAM-UP-43-1234567',consent=True)
    r=client.post('/api/orders/'+o['id']+'/registry',headers=h,json=body)
    assert r.status_code==200,r.text
    assert r.json()['outcome']=='matched' and not r.json()['full_kyc_complete']
    assert 'social_category' not in r.text
    assert calls[0][0]=='https://sandbox.cashfree.com/verification/udyam'
    assert client.get('/api/orders/'+o['id'],headers=h).json()['kyc_status']=='pending'
    body['consent']=False
    assert client.post('/api/orders/'+o['id']+'/registry',headers=h,json=body).status_code==400


def test_whatsapp_shared_planner_no_purchase_and_stop():
    state={}
    for message in ['START','YES','clinic','12','1','1','YES','email, backup','email, 700, 5','1500']:
        state,reply=respond(state,message)
    assert state['step']=='done'
    assert 'Keep 1' in reply and 'Cloud Backup' in reply
    assert 'Cloud Email' not in reply
    assert 'No order or payment' in reply
    assert respond(state,'STOP')[0]=={}


def test_whatsapp_signatures_duplicate_delivery_window_and_phone_binding(monkeypatch):
    for key,value in dict(APP_SECRET='app-fixture',PHONE_NUMBER_ID='123456789',TOKEN='token-fixture',GRAPH_VERSION='v26.0',VERIFY_TOKEN='verify-fixture').items():
        monkeypatch.setenv('WHATSAPP_'+key,value)
    sent=[]
    async def send(phone,text):
        sent.append((phone,text));return {'messages':[{'id':'out1'}]}
    monkeypatch.setattr(providers,'send_whatsapp',send)
    assert client.get('/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-fixture&hub.challenge=1234').text=='1234'
    assert client.get('/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong').status_code==403
    message=dict(id='wamid.fixture1',**{'from':'919876543210'},timestamp=str(int(time.time())),type='text',text={'body':'START'})
    def wrap(m):
        return dict(object='whatsapp_business_account',entry=[dict(changes=[dict(value=dict(metadata={'phone_number_id':'123456789'},messages=[m]))])])
    body=wrap(message)
    assert client.post('/api/webhooks/whatsapp',json=body).status_code==401
    assert signed('/api/webhooks/whatsapp',body,'app-fixture','x-hub-signature-256','sha256=').status_code==200
    assert signed('/api/webhooks/whatsapp',body,'app-fixture','x-hub-signature-256','sha256=').status_code==200
    assert len(sent)==1
    old=wrap({**message,'id':'wamid.old','timestamp':str(int(time.time())-90000)})
    signed('/api/webhooks/whatsapp',old,'app-fixture','x-hub-signature-256','sha256=')
    assert len(sent)==1
    signed('/api/webhooks/whatsapp',wrap({**message,'id':'wamid.consent','text':{'body':'YES'}}),'app-fixture','x-hub-signature-256','sha256=')
    o,_=new_order(['email'])
    signed('/api/webhooks/whatsapp',wrap({**message,'id':'wamid.track','text':{'body':'TRACK '+o['id']}}),'app-fixture','x-hub-signature-256','sha256=')
    assert o['id'] in sent[-1][1]
    with mod._conn() as c:
        c.execute('UPDATE orders SET phone=? WHERE id=?',('911111111111',o['id']))
    signed('/api/webhooks/whatsapp',wrap({**message,'id':'wamid.other','text':{'body':'TRACK '+o['id']}}),'app-fixture','x-hub-signature-256','sha256=')
    assert 'No matching order' in sent[-1][1]


def test_whatsapp_uncertain_send_is_not_retried(monkeypatch):
    for key in ('APP_SECRET','TOKEN','GRAPH_VERSION'):
        monkeypatch.setenv('WHATSAPP_'+key,'fixture')
    monkeypatch.setenv('WHATSAPP_PHONE_NUMBER_ID','123')
    calls=[]
    async def fail(*a):
        calls.append(1);raise HTTPException(502,'unknown')
    monkeypatch.setattr(providers,'send_whatsapp',fail)
    body=dict(object='whatsapp_business_account',entry=[dict(changes=[dict(value=dict(metadata={'phone_number_id':'123'},messages=[dict(id='wamid.failed',**{'from':'919876543219'},timestamp=str(int(time.time())),type='text',text={'body':'START'})]))])])
    for _ in range(2):
        assert signed('/api/webhooks/whatsapp',body,'fixture','x-hub-signature-256','sha256=').status_code==200
    assert len(calls)==1


def test_provider_http_errors_do_not_return_body_or_mock_success(monkeypatch):
    import asyncio
    import httpx
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda req:httpx.Response(500,json={'secret':'must not leak'}))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(providers.request('GET','https://api.example.test'))
    assert caught.value.status_code==502
    assert 'must not leak' not in str(caught.value.detail)
