import copy
import pytest
import calibration
import config
import mega_control
from flask_server import flask_app
from test_system import isolated, connected

PAIRS = [
 ('401_686', {'adc401':1000.25, 'adc686':900.125}, 1),
 ('686_918', {'adc686':900.125, 'adc918':800.5}, 0),
 ('401_918', {'adc401':1000.25, 'adc918':800.5}, 2),
]

@pytest.mark.parametrize('pair,values,code',PAIRS)
def test_pair_roundtrip_and_only_selected_adc_on_wire(pair,values,code):
 connected();calibration.set_active(True)
 profile={'ph':dict(points=2,pair=pair,**values),'turbidity':None}
 calibration.save(profile);calibration.initialize()
 assert calibration.status()['profile']==profile
 _, payload=mega_control.commands()
 assert f'PP:{code}' in payload and 'PC:2' in payload
 for key,field in [('adc401','A4'),('adc686','A6'),('adc918','A9')]:
  assert f'{field}:{values.get(key,0):.4f}' in payload
 assert all(len(line)<256 for line in payload.splitlines())

@pytest.mark.parametrize('pair,values,code',PAIRS)
@pytest.mark.parametrize('fault',['missing','reversed','equal','close','rail','nan','unused'])
def test_pair_validation_does_not_replace_profile(pair,values,code,fault):
 calibration.set_active(True)
 profile={'ph':dict(points=2,pair=pair,**values),'turbidity':None}
 calibration.save(profile);bad=copy.deepcopy(profile);ph=bad['ph'];lo,hi=calibration.PH_PAIRS[pair]
 if fault=='missing':del ph[lo]
 if fault=='reversed':ph[lo],ph[hi]=ph[hi],ph[lo]
 if fault=='equal':ph[lo]=ph[hi]
 if fault=='close':ph[lo]=ph[hi]+19.99
 if fault=='rail':ph[hi]=0
 if fault=='nan':ph[lo]=float('nan')
 if fault=='unused':ph[next(k for k in calibration.PH_BUFFERS if k not in values)]=700
 with pytest.raises(ValueError):calibration.save(bad)
 assert calibration.status()['profile']==profile

@pytest.mark.parametrize('pair',['bad',None,[],True])
def test_invalid_pair_rejected(pair):
 with pytest.raises(ValueError):calibration.validate({'ph':dict(points=2,pair=pair,adc686=900,adc918=800),'turbidity':None})


def test_switch_every_pair_then_three_points_through_admin_api(monkeypatch):
 monkeypatch.setattr(config,'DEVELOPER_OPTIONS',True)
 c=flask_app.test_client();c.post('/api/auth/login',json={'pin':config.ADMIN_PIN})
 c.post('/api/developer/calibration',json={'active':True})
 for pair,values,_ in PAIRS:
  p={'ph':dict(points=2,pair=pair,**values),'turbidity':None}
  assert c.put('/api/developer/calibration',json=p).status_code==200
  assert c.get('/api/developer/calibration').json['calibration']['profile']==p
 p={'ph':dict(points=3,adc401=1000,adc686=900,adc918=800),'turbidity':None}
 assert c.put('/api/developer/calibration',json=p).status_code==200
 assert 'PC:3,A4:1000.0000,PP:0' in mega_control.commands()[1]
