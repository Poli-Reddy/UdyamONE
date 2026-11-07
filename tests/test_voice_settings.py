"""Speech boundaries and staff entry use the same API policy as operations."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import app as mod

client=TestClient(mod.app)


def test_staff_entry_requires_key_when_configured(monkeypatch):
    monkeypatch.setattr(mod,'ADMIN_TOKEN','staff-test-key')
    monkeypatch.setenv('UDYAM_DEMO','false')
    for headers in ({},{'x-admin-token':'wrong'},{'x-order-token':'staff-test-key'}):
        assert client.get('/api/operator/session',headers=headers).status_code==401
    response=client.get('/api/operator/session',headers={'x-admin-token':'staff-test-key'})
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'
    assert response.json()=={'authorized':True,'authentication':'staff_token'}


def test_unconfigured_production_staff_entry_is_closed(monkeypatch):
    monkeypatch.setattr(mod,'ADMIN_TOKEN','')
    monkeypatch.setenv('UDYAM_DEMO','false')
    assert client.get('/api/operator/session').status_code==503
    monkeypatch.setenv('UDYAM_DEMO','true')
    assert client.get('/api/operator/session').json()['authentication']=='local_demo'


def test_blank_voice_input_never_calls_provider(monkeypatch):
    async def no_call(*args):
        pytest.fail('Provider must not receive blank speech')
    monkeypatch.setattr(mod,'_generate_tts',no_call)
    assert client.post('/api/tts',json={'text':' \t\n '}).status_code==422


def test_speech_selects_language_and_gender_and_returns_private_audio(monkeypatch):
    calls=[]
    audio=b'ID3'+bytes(2048)
    async def generate(text,voice,rate):
        calls.append((text,voice,rate))
        return audio
    monkeypatch.setattr(mod,'_generate_tts',generate)
    monkeypatch.setattr(mod,'_TTS_CACHE',mod.OrderedDict())
    for lang in mod.TTS_VOICES:
        for gender in ('female','male'):
            response=client.post('/api/tts',json={'text':'  Voice    test  ','lang':lang,'gender':gender})
            assert response.status_code==200
            assert response.headers['content-type']=='audio/mpeg'
            assert response.headers['cache-control']=='no-store'
            assert response.headers['x-tts-voice']==mod.TTS_VOICES[lang][gender]
            assert response.content==audio
    assert len(calls)==8
    assert all(call[0]=='Voice test' for call in calls)
    client.post('/api/tts',json={'text':'Voice test','lang':'en','gender':'female'})
    assert len(calls)==8


@pytest.mark.parametrize('status',[503,504])
def test_provider_failure_stays_an_error_for_device_fallback(monkeypatch,status):
    async def unavailable(*args):
        raise HTTPException(status,'Voice service unavailable')
    monkeypatch.setattr(mod,'_generate_tts',unavailable)
    monkeypatch.setattr(mod,'_TTS_CACHE',mod.OrderedDict())
    response=client.post('/api/tts',json={'text':'A connection test'})
    assert response.status_code==status
    assert 'audio/mpeg' not in response.headers['content-type']


def service_plan(needs,monthly=700,budget=None):
    profile=dict(tier=1,employees=12,sites=1,gstin=True,type='clinic',needs=needs,context=dict(locations=[dict(id='main',name='Clinic',employees=12,needs=needs)],owned=[dict(site_id='main',capability='email',quantity=5,adequacy='adequate',monthly=monthly)],inventory_confirmed=True,budget=budget))
    response=client.post('/api/recommend',json=profile)
    assert response.status_code==200
    return response.json()


def test_spoken_plan_keeps_existing_costs_separate_and_never_invents_products():
    plan=service_plan(['email','backup'])
    speech=plan['speech_text']
    assert '599' in speech and '1,299' in speech
    assert 'Existing provider bills remain separate' in speech
    assert 'mobile lines' not in speech and 'connectivity' not in speech


def test_spoken_plan_explains_nothing_to_buy_and_blocks_unready_acceptance():
    covered=service_plan(['email'])
    assert 'nothing new to buy' in covered['speech_text']
    assert 'contract' not in covered['speech_text']
    blocked=service_plan(['email','backup'],budget=1000)
    assert 'before this plan can be accepted' in blocked['speech_text']


def test_spoken_plan_does_not_call_partial_costs_the_combined_total():
    unknown=service_plan(['email','backup'],monthly=None)
    assert unknown['plan']['ready']
    assert 'combined monthly total is not yet known' in unknown['speech_text']
