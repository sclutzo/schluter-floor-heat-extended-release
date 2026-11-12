"""Offline regression tests for the cleaned integration's bundled API and metadata."""
import importlib
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch
import tempfile
from datetime import datetime, timedelta, UTC

ROOT = pathlib.Path(__file__).resolve().parents[1] / 'custom_components' / 'schluter_heat_extended'
package = types.ModuleType('schluter_heat_extended')
package.__path__ = [str(ROOT)]
sys.modules['schluter_heat_extended'] = package
api_mod = importlib.import_module('schluter_heat_extended.v_lib.api')
auth_mod = importlib.import_module('schluter_heat_extended.v_lib.authenticator')

class Response:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
    def json(self): return self._data

class MockLoginSession:
    def __init__(self, data):
        self.data = data
        self.trust_env = True
        self.headers = {}
        self.mount_calls = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def mount(self, *args): self.mount_calls.append(args)
    def post(self, url, json=None, **kwargs):
        self.last_url = url
        self.last_json = json
        self.last_kwargs = kwargs
        return Response(self.data)

class ApiTests(unittest.TestCase):
    def make_api(self): return api_mod.Api(email='test@example.invalid', password='placeholder')

    def test_login_application7_publishes_and_caches(self):
        client = self.make_api()
        mock = MockLoginSession({'ErrorCode': 0,'SessionId':'test-session'})
        with tempfile.TemporaryDirectory() as tmp, patch.object(api_mod.requests, 'Session', return_value=mock):
            cache = pathlib.Path(tmp)/'.schluter_heat_extended.conf'
            a = auth_mod.Authenticator(client,'test@example.invalid','placeholder',str(cache))
            result = a.authenticate()
            self.assertEqual(result.state, auth_mod.AuthenticationState.AUTHENTICATED)
            self.assertEqual(client._session_id,'test-session')
            self.assertEqual(mock.last_json['Application'],7)
            self.assertIs(mock.last_kwargs['allow_redirects'], False)
            self.assertEqual(json.loads(cache.read_text())['session_id'],'test-session')
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def test_authentication_rejects_unacknowledged_login(self):
        client=self.make_api(); mock=MockLoginSession({'ErrorCode': 8,'SessionId':'invalid'})
        with patch.object(api_mod.requests,'Session',return_value=mock):
            self.assertEqual(auth_mod.Authenticator(client,'a','b').authenticate().state,
                             auth_mod.AuthenticationState.REQUIRES_AUTHENTICATION)
            self.assertIsNone(client._session_id)

    def test_cached_session_reused(self):
        client=self.make_api()
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'.schluter_heat_extended.conf'
            path.write_text(json.dumps({'application':7,'state':'authenticated',
                'session_id':'cached', 'expires':(datetime.now(UTC)+timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}))
            a=auth_mod.Authenticator(client,'a','b',str(path))
            self.assertEqual(a.authenticate().session_id,'cached')
            self.assertEqual(client._session_id,'cached')

    def test_discovery_group_and_serials(self):
        client=self.make_api(); client._session_id='test-session'
        with patch.object(api_mod.requests,'get',return_value=Response({'Groups':[{'Thermostats':[{'SerialNumber':'123', 'Name':'Bathroom'}]}]})) as get:
            found=client.get_thermostats()
            self.assertEqual([t.serial_number for t in found],['123'])
            self.assertEqual(get.call_args.kwargs['params'],{'sessionid':'test-session'})
            self.assertNotIn('Cookie',get.call_args.kwargs['headers'])
            self.assertIs(get.call_args.kwargs['allow_redirects'], False)

    def test_empty_groups_refresh_retries(self):
        client=self.make_api(); client._session_id='old'
        def refresh(**kwargs): client._session_id='new';return Response({'SessionId':'new','ErrorCode':0})
        with patch.object(client,'get_session',side_effect=refresh) as auth, patch.object(api_mod.requests,'get',side_effect=[Response({'Groups':[]}),Response({'Groups':[{'Thermostats':[{'SerialNumber':'456'}]}]})]) as get:
            found=client.get_thermostats()
            self.assertEqual([t.serial_number for t in found],['456'])
            auth.assert_called_once()
            self.assertEqual(get.call_args.kwargs['params'],{'sessionid':'new'})

    def test_401_refresh_retries(self):
        client=self.make_api();client._session_id='old'
        def refresh(**kwargs):client._session_id='new';return Response({'SessionId':'new','ErrorCode':0})
        with patch.object(client,'get_session',side_effect=refresh) as auth, patch.object(api_mod.requests,'get',side_effect=[Response({},401),Response({'Thermostats':[{'SerialNumber':'789'}]})]):
            self.assertEqual([t.serial_number for t in client.get_thermostats()],['789'])
            auth.assert_called_once()

    def test_locations_fallback(self):
        client=self.make_api()
        with patch.object(api_mod.requests,'get',return_value=Response({})), patch.object(client,'_call_api',return_value=Response([{'thermostats':[{'SerialNumber':'0001'}]}])):
            self.assertEqual([t.serial_number for t in client.get_thermostats()],['0001'])

    def test_energy_uses_active_session_and_target_serial(self):
        client=self.make_api();client._session_id='active'
        with patch.object(api_mod.requests,'get',return_value=Response({'Success':True,'EnergyUsage':[]})) as get:
            result=client.get_energy_usage('serial-two','day','2026-11-04')
            self.assertTrue(result['Success'])
            self.assertEqual(get.call_args.kwargs['params']['serialnumber'],'serial-two')
            self.assertEqual(get.call_args.kwargs['params']['sessionid'],'active')

    def test_temporary_temperature_controls_url(self):
        client=self.make_api();client._session_id='active'
        with patch.object(api_mod.requests,'request',return_value=Response({'Success':True})) as req:
            result=client.set_temperature('serial-123',22.5,duration_minutes=30)
            self.assertTrue(result['Success'])
            self.assertEqual(req.call_args.kwargs['json']['ComfortTemperature'],'2250')
            self.assertEqual(req.call_args.kwargs['params']['serialnumber'],'serial-123')
            self.assertNotIn('Cookie',req.call_args.kwargs['headers'])
            self.assertIs(req.call_args.kwargs['allow_redirects'], False)

    def test_return_to_schedule_controls_serial(self):
        client=self.make_api();client._session_id='active'
        with patch.object(api_mod.requests,'request',return_value=Response({'Success':True})) as req:
            self.assertEqual(client.return_to_schedule('other-serial',False),{'Success':True})
            self.assertEqual(req.call_args.kwargs['params']['serialnumber'],'other-serial')
            self.assertEqual(req.call_args.kwargs['json']['RegulationMode'],1)

    def test_authenticated_api_calls_disallow_redirects(self):
        client = self.make_api()
        client._session_id = 'private-test-session'
        with patch.object(client._http_session, 'request',
                          return_value=Response({'Success': True})) as request:
            self.assertEqual(
                client._call_api('get', api_mod.API_GET_LOCATIONS_URL).status_code,
                200,
            )
            self.assertIs(request.call_args.kwargs['allow_redirects'], False)

    def test_schedule_and_return_requests_disallow_redirects(self):
        client = self.make_api()
        client._session_id = 'private-test-session'
        with patch.object(api_mod.requests, 'request',
                          return_value=Response({'Success': True})) as request:
            client.return_to_schedule('serial-placeholder', False)
            self.assertIs(request.call_args.kwargs['allow_redirects'], False)
        with patch.object(api_mod.requests, 'get',
                          return_value=Response({'Success': True, 'EnergyUsage': []})) as request:
            client.get_energy_usage('serial-placeholder', 'day', '2026-11-04')
            self.assertIs(request.call_args.kwargs['allow_redirects'], False)

    def test_no_developer_exposure(self):
        client=self.make_api()
        for n in ('_init_kwargs','diagnose_application7','update_developer_options',
                  '_parse_forced_curl','_write_debug_dump'):
            self.assertFalse(hasattr(client,n), n)

    def test_service_selector_and_status_metadata(self):
        import yaml
        services=yaml.safe_load((ROOT/'services.yaml').read_text())
        self.assertEqual(set(services),{'get_energy_statistics_status','get_energy_report',
                'get_schedule','save_schedule','set_temporary_temperature','return_to_schedule',
                'inspect_time_settings'})
        for n, svc in services.items():
            if n!='get_energy_statistics_status':
                self.assertEqual(svc['target']['entity']['integration'],'schluter_heat_extended')
        m=json.loads((ROOT/'manifest.json').read_text())
        self.assertEqual(m['domain'],'schluter_heat_extended')
        self.assertEqual(m['version'],'1.1.0b2')
        self.assertTrue(m['config_flow'])

    def test_recorder_query_uses_set_ids(self):
        text=(ROOT/'energy_statistics.py').read_text()
        self.assertIn('{self.statistic_id}, "hour",',text)
        self.assertIn('self.statistic_id = f"{DOMAIN}:energy_{self.serial}"',text)

    def test_safe_no_live_identity_or_debug_files(self):
        for p in ROOT.rglob('*'):
            if p.is_file() and p.suffix in ('.py','.yaml'):
                content=p.read_text()
                self.assertNotIn('scluter_heat_extended',content)
                self.assertNotIn('schluter_heat_extended_cookies.json',content)

if __name__=='__main__':
    unittest.main(verbosity=2)
