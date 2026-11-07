from fastapi.testclient import TestClient
import pytest
import app as mod
from test_workflow import new_order

client=TestClient(mod.app)


@pytest.mark.parametrize('field,value',[
    ('business_name','   '),('contact_name',' '),('address','    '),
    ('phone','abcdefghij'),('phone','12345-----'),('email','invalid email'),
])
def test_checkout_rejects_invalid_customer_details(field,value):
    _,body=new_order(['email'])
    body[field]=value
    assert client.post('/api/orders',json=body).status_code==422


def test_formatted_phone_is_normalised_and_optional_email_stays_optional():
    _,body=new_order(['email'])
    body.update(phone='+91 (98765) 43210',business_name='  New Clinic  ',email='')
    result=client.post('/api/orders',json=body)
    assert result.status_code==200,result.text
    assert result.json()['phone']=='+919876543210'
    assert result.json()['business_name']=='New Clinic'


def test_staff_order_read_is_separate_from_customer_authorisation(monkeypatch):
    order,_=new_order(['email'])
    monkeypatch.setenv('UDYAM_DEMO','false')
    monkeypatch.setattr(mod,'ADMIN_TOKEN','staff-test-key')
    assert client.get('/api/operator/orders/'+order['id']).status_code==401
    assert client.get('/api/operator/orders/'+order['id'],headers={'x-admin-token':'staff-test-key'}).status_code==200
    assert client.get('/api/orders/'+order['id'],headers={'x-admin-token':'staff-test-key'}).status_code==401
