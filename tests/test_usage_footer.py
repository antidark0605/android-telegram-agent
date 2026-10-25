"""Synthetic quota schema, failure, cache, and task-scoping regressions."""
import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usage_footer import QuotaService, parse_quota
from presentation import (directory_listing_messages, progress_message,
                          reply_markdown, stop_markup,
                          usage_markdown, usage_plain, usage_view)


def format_usage(model, data, at=None, cached=False):
    return usage_markdown(model, data, cached)


def decorate_reply(text, model, data, at=None, cached=False):
    return reply_markdown(text, model, data, cached)


def fixture():
    return {'status':'SUCCESS', 'num_turns':0,
            'command':{'name':'usage', 'data':{'groups':[
        {'name':'Gemini models',
         'buckets':[{'id':bid, 'window':window, 'remaining_fraction':fraction,
                     'reset_time':'2030-01-02T03:04:05Z'}
                    for bid,window,fraction in [('gemini-5h','5h',.8),
                                                ('gemini-weekly','weekly',.7)]]},
        {'name':'Claude and GPT models',
         'buckets':[{'id':bid, 'window':window, 'remaining_fraction':fraction,
                     'reset_time':'2030-01-02T03:04:05Z'}
                    for bid,window,fraction in [('3p-5h','5h',.2),
                                                ('3p-weekly','weekly',.1)]]}]}}}


def argon_fixture():
    document=fixture()
    document['command']['data']['groups'].append({
        'name':'Gemini 4 Argon',
        'buckets':[{'id':'argon-weekly','window':'weekly',
                    'remaining_fraction':0.557590663433075,
                    'reset_time':'2030-01-03T03:04:05Z'}]})
    return document


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

    def test_argon_uses_own_weekly_group_and_explicit_na(self):
        data=parse_quota(argon_fixture())
        footer=format_usage('gemini-4-argon-high',data)
        self.assertIn('Context: unavailable',footer)
        self.assertIn('5h: N/A',footer)
        self.assertIn('Weekly: 44.24% used · 55.76% left',footer)
        self.assertIn('01/03 11:04',footer)
        self.assertNotIn('30.0% used',footer)
        display=format_usage('Gemini 4 Argon (Medium)',data)
        self.assertIn('44.24% used',display)
        self.assertIn('5h: N/A',display)

    def test_argon_missing_group_never_substitutes_gemini(self):
        data=parse_quota(fixture())
        footer=format_usage('gemini-4-argon-low',data)
        self.assertIn('5h: N/A',footer)
        self.assertIn('Weekly: unavailable',footer)
        self.assertNotIn('30.0% used',footer)

    def test_headless_usage_requires_zero_turn_and_named_family(self):
        for update in ({'num_turns':1},{'num_turns':False},{'status':'ERROR'},
                       {'command':{'name':'other','data':{'groups':[]}}}):
            self.assertEqual(parse_quota({**argon_fixture(),**update}),{})
        unnamed=argon_fixture()
        for group in unnamed['command']['data']['groups']:
            group.pop('name')
        self.assertEqual(parse_quota(unnamed),{})

    def test_unknown_failed_denied_or_model_prose_not_trusted(self):
        for doc in [None,{}, {'status':'SUCCESS','num_turns':0,'response':'99% remaining'},
                    {**fixture(),'error':'secret'}, {**fixture(),'denied_actions':[1]},
                    {**fixture(),'status':'ERROR'},
                    {'status':'SUCCESS','command':{'name':'other','data':fixture()['command']['data']}}]:
            self.assertEqual(parse_quota(doc),{})

    def test_bad_values_and_duplicate_bucket_rejected(self):
        for value in [True,'80%',-1,1.1,float('nan'),float('inf'),10**1000]:
            doc=fixture();doc['command']['data']['groups'][0]['buckets'][0]['remaining_fraction']=value
            self.assertNotIn('gemini-5h',parse_quota(doc))
        for field,value in [('window','unknown')]:
            doc=fixture();doc['command']['data']['groups'][0]['buckets'][0][field]=value
            self.assertNotIn('gemini-5h',parse_quota(doc))
        doc=fixture();b=doc['command']['data']['groups'][0]['buckets'];b.append(dict(b[0]))
        self.assertEqual(parse_quota(doc),{})

    def test_missing_or_invalid_reset_keeps_verified_fraction(self):
        for reset in (None, "invalid", "2030-01-01T00:00:00"):
            doc=argon_fixture()
            bucket=doc['command']['data']['groups'][-1]['buckets'][0]
            if reset is None:
                bucket.pop('reset_time')
            else:
                bucket['reset_time']=reset
            footer=format_usage('gemini-4-argon-high',parse_quota(doc))
            self.assertIn('Weekly: 44.24% used · 55.76% left',footer)
            self.assertNotIn('resets',footer)

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
        self.assertIn('⚡ **[AGY]** · gemini\\-test',message)
        self.assertNotIn('Selected model:',message)
        self.assertIn(text,message)
        self.assertIn('5h: unavailable',message)
        self.assertNotIn('Quota checked',message)


    def test_visible_gaps_and_quote_paragraph_boundaries(self):
        data=parse_quota(fixture())
        at=datetime(2030,1,1,tzinfo=timezone.utc)
        message=decorate_reply('Synthetic body', 'gemini-test', data, at)
        self.assertIn('gemini\\-test\n\n\u00a0\n\nSynthetic body',message)
        self.assertIn('Synthetic body\n\n\u00a0\n\n> 📊',message)
        footer=format_usage('gemini-test', data, at)
        self.assertEqual(len(footer.split('\n>\n')),4)
        self.assertIn('> Context: unavailable\n>\n> 5h:',footer)
        self.assertTrue(message.startswith('⚡ **[AGY]** · gemini\\-test\n\n'))


class PresentationTests(unittest.TestCase):
    def test_directory_listing_html_is_escaped_and_utf16_bounded(self):
        from vendor.telegram_ui import utf16_length
        body = ("😀 & <entry>\n" * 900)
        messages = directory_listing_messages("/safe/<cwd>", body)
        self.assertGreater(len(messages), 1)
        self.assertTrue(messages[0][0].startswith("<b>📂 Directory listing</b>"))
        self.assertIn("<code>/safe/&lt;cwd&gt;</code>", messages[0][0])
        self.assertTrue(all(utf16_length(formatted) <= 4096
                            for formatted, _plain in messages))
        self.assertTrue(all("&lt;entry&gt;" in formatted
                            for formatted, _plain in messages))
        reconstructed = messages[0][1].split("—————\n", 1)[1]
        reconstructed += "".join(plain for _formatted, plain in messages[1:])
        self.assertEqual(reconstructed, body)

    def test_body_and_standalone_artifact_marker_preserved_exactly(self):
        import tempfile
        from artifacts import extract_artifacts
        from vendor.telegram_ui.rendering import GAP
        body='# Title\n\n```\nTAGW_MEDIA: {"path":"artifacts/fenced.txt"}\n```\n| a | b |\n\\*raw\\* 😀\nTAGW_MEDIA: {"path":"artifacts/out.txt"}'
        message=reply_markdown(body,'gemini-test',{},False)
        header,_,rest=message.partition(GAP)
        self.assertEqual(header,'⚡ **[AGY]** · gemini\\-test')
        self.assertTrue(rest.startswith(body+GAP+'> 📊'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'artifacts').mkdir(); (root/'artifacts'/'out.txt').write_text('x')
            cleaned,media=extract_artifacts(message,root)
        self.assertEqual([m.filename for m in media],['out.txt'])
        self.assertIn('artifacts/fenced.txt',cleaned)

    def test_invalid_or_missing_model_label_falls_back(self):
        for model in [None,'','bad\nmodel','x'*301]:
            message=reply_markdown('Body',model,{},False)
            self.assertTrue(message.startswith('⚡ **[AGY]** · unavailable\n'))
            self.assertIn('\n\nBody\n\n',message)

    def test_usage_mapping_unknown_and_task_tokens_never_context(self):
        data=parse_quota(fixture())
        view=usage_view('gemini-test',data,True)
        self.assertIsNone(view.context)
        self.assertAlmostEqual(view.five_hour.remaining_percent,80.0)
        self.assertEqual(view.five_hour.resets_at.utcoffset().total_seconds(),8*3600)
        self.assertTrue(view.cached)
        unknown=usage_view('example-model',data)
        self.assertIsNone(unknown.five_hour); self.assertIsNone(unknown.weekly)
        broken=usage_view('gemini-test',{'gemini-5h':{'left':float('nan'),'reset':None}})
        self.assertIsNone(broken.five_hour)
        plain=usage_plain('claude-test',data,False)
        self.assertEqual(plain.splitlines()[:2],['📊 **Usage**','Context: unavailable'])
        self.assertIn('5h: 80.0% used · 20.0% left · resets 01/02 11:04',plain)
        self.assertNotIn('+0800',plain)
        self.assertNotIn('>',plain)

    def test_progress_states_markup_and_html(self):
        text,markup=progress_message('running',100.0,'abc_123-Z',now=165.9)
        self.assertIn('<b>任務進度</b>',text)
        self.assertIn('<b>目前</b>：執行中',text)
        self.assertIn('<b>已執行</b>：65 秒',text)
        self.assertNotIn('流程',text)
        self.assertNotIn('**',text)
        self.assertEqual(markup,{'inline_keyboard':[[{'text':'🛑 中斷','callback_data':'stop:abc_123-Z'}]]})
        self.assertNotIn('task:cancel',str(markup))
        waiting,wm=progress_message('waiting',0.0,'k1',now=1.0)
        self.assertIn('等待回覆',waiting); self.assertEqual(wm['inline_keyboard'][0][0]['callback_data'],'stop:k1')
        delivering,dm=progress_message('delivering',0.0,'k1',now=1.0)
        self.assertIn('Delivering',delivering); self.assertNotIn('已完成',delivering)
        self.assertEqual(dm,{'inline_keyboard':[]})
        unconfirmed,um=progress_message('unconfirmed',0.0,'k1',now=1.0)
        self.assertIn('Delivery unconfirmed',unconfirmed); self.assertNotIn('已完成',unconfirmed)
        self.assertEqual(um,{'inline_keyboard':[]})
        for state,label in [('completed','已完成'),('cancelled','已取消'),('failed','失敗')]:
            text,markup=progress_message(state,0.0,'k1',now=3.0)
            self.assertIn(label,text); self.assertEqual(markup,{'inline_keyboard':[]})
        # A clock earlier than the start never shows negative elapsed time.
        self.assertIn('0 秒',progress_message('running',10.0,'k1',now=5.0)[0])
        for state,key in [('unknown','k1'),('running','bad key'),('running','x'*49)]:
            with self.assertRaises(ValueError):
                progress_message(state,0.0,key,now=0.0)

    def test_stop_translation_rejects_foreign_callbacks(self):
        good={'inline_keyboard':[[{'text':'🛑 中斷','callback_data':'task:cancel:k1'}]]}
        self.assertEqual(stop_markup(good,'k1')['inline_keyboard'][0][0]['callback_data'],'stop:k1')
        for data in ['task:cancel:k2','other:k1','stop:k1']:
            with self.assertRaises(ValueError):
                stop_markup({'inline_keyboard':[[{'text':'x','callback_data':data}]]},'k1')


if __name__=='__main__':
    unittest.main()
