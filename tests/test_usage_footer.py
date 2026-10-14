"""Synthetic quota schema, failure, cache, and task-scoping regressions."""
import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usage_footer import QuotaService, parse_quota, format_usage, decorate_reply


def fixture():
    return {'status':'SUCCESS', 'command':{'name':'usage', 'data':{'groups':[
        {'buckets':[{'id':bid, 'window':window, 'remaining_fraction':fraction,
                     'reset_time':'2030-01-02T03:04:05Z'}
                    for bid,window,fraction in [('gemini-5h','5h',.8),
                                                ('gemini-weekly','weekly',.7),
                                                ('3p-5h','5h',.2),
                                                ('3p-weekly','weekly',.1)]]}]}}}


class UsageTests(unittest.TestCase):
    def test_actual_command_shape_and_family_mapping(self):
        data=parse_quota(fixture())
        self.assertEqual(len(data),4)
        at=datetime(2030,1,1,tzinfo=timezone.utc)
        g=format_usage('gemini-test',data,at)
        c=format_usage('claude-test',data,at,True)
        self.assertIn('5h: 20.0% used · 80.0% left',g)
        self.assertIn('Weekly: 30.0% used · 70.0% left',g)
        self.assertIn('01/02 11:04',g)
        self.assertIn('80.0% used · 20.0% left',c)
        self.assertIn('cached',c)
        self.assertIn('Context: unavailable',g)
        self.assertIn('5h: unavailable',format_usage('unknown',data,at))

    def test_unknown_failed_denied_or_model_prose_not_trusted(self):
        for doc in [None,{}, {'status':'SUCCESS','response':'99% remaining'},
                    {**fixture(),'error':'secret'}, {**fixture(),'denied_actions':[1]},
                    {**fixture(),'status':'ERROR'},
                    {'status':'SUCCESS','command':{'name':'other','data':fixture()['command']['data']}}]:
            self.assertEqual(parse_quota(doc),{})

    def test_bad_values_and_duplicate_bucket_rejected(self):
        for value in [True,'80%',-1,1.1,float('nan'),float('inf'),10**1000]:
            doc=fixture();doc['command']['data']['groups'][0]['buckets'][0]['remaining_fraction']=value
            self.assertNotIn('gemini-5h',parse_quota(doc))
        for field,value in [('id',[]),('window','weekly'),('reset_time','invalid'),
                            ('reset_time','2030-01-01T00:00:00'),
                            ('reset_time','0001-01-01T00:00:00+23:00'),
                            ('reset_time','9999-12-31T23:59:59-23:00')]:
            doc=fixture();doc['command']['data']['groups'][0]['buckets'][0][field]=value
            self.assertNotIn('gemini-5h',parse_quota(doc))
        doc=fixture();b=doc['command']['data']['groups'][0]['buckets'];b.append(dict(b[0]))
        self.assertEqual(parse_quota(doc),{})

    def test_cache_expiry_failure_does_not_reuse_stale_data(self):
        svc=QuotaService('synthetic-agy',Path('/synthetic'))
        with patch('usage_footer.time.monotonic',side_effect=[0,0,1,61,61]), patch(
                'usage_footer.subprocess.run',side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps(fixture())),subprocess.TimeoutExpired('synthetic',20)]) as run:
            self.assertEqual(len(svc.get()[0]),4)
            self.assertTrue(svc.get()[2])
            self.assertEqual(svc.get()[0],{})
            self.assertEqual(run.call_count,2)
            self.assertNotIn('--conversation',run.call_args.args[0])
            self.assertNotIn('--dangerously-skip-permissions',run.call_args.args[0])

    def test_failed_exit_and_invalid_json_fail_closed(self):
        for result in [SimpleNamespace(returncode=1,stdout=json.dumps(fixture())),
                       SimpleNamespace(returncode=0,stdout='not json'),
                       SimpleNamespace(returncode=0,stdout='['*1100+'0'+']'*1100),
                       SimpleNamespace(returncode=0,stdout='x'*100001)]:
            with patch('usage_footer.subprocess.run',return_value=result):
                self.assertEqual(QuotaService('fake',Path('/fake')).get()[0],{})

    def test_header_and_marker_are_preserved(self):
        text='Answer\nTAGW_MEDIA: {"path":"artifacts/example.txt"}'
        message=decorate_reply(text,'gemini-test',{},None)
        self.assertIn('Selected model: `gemini-test`',message)
        self.assertIn(text,message)
        self.assertIn('5h: unavailable',message)


    def test_visible_gaps_and_quote_paragraph_boundaries(self):
        data=parse_quota(fixture())
        at=datetime(2030,1,1,tzinfo=timezone.utc)
        message=decorate_reply('Synthetic body', 'gemini-test', data, at)
        self.assertIn('`gemini-test`\n\n\u00a0\n\nSynthetic body',message)
        self.assertIn('Synthetic body\n\n\u00a0\n\n> 📊',message)
        footer=format_usage('gemini-test', data, at)
        self.assertEqual(len(footer.split('\n>\n')),5)
        self.assertIn('> Context: unavailable\n>\n> 5h:',footer)


if __name__=='__main__':
    unittest.main()
