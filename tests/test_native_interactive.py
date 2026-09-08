"""Unsigned deterministic app-server protocol fixtures, never native evidence."""
import io
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime' / 'delivery'))
import native_process as n

class InteractiveTests(unittest.TestCase):
    def test_frozen_answer_is_reserved_before_send_and_correlated(self):
        self.assertTrue(hasattr(n, 'Interactive'), 'interactive transport must be integrated')
        claims=[]
        q=[{'id':'choice','header':'Choice','question':'Pick one'}]
        p={'schema_version':'devforge.native-answer-policy/v1','steps':[{'unit_id':'answer-1','action':'answer','questions':q,'answers':{'choice':{'answers':['A']}}}]}
        t=n.Interactive(p, b'hello', '/fixture', lambda unit, message: claims.append((unit,message)), io.BytesIO())
        self.assertEqual(t.start()['method'],'initialize')
        t.receive({'id':'init','result':{}})
        messages=t.receive({'id':'thread','result':{'thread':{'id':'t'}}})
        self.assertEqual(claims[0][0],'initial')
        self.assertEqual(messages[0]['method'],'turn/start')
        t.receive({'id':'turn-0','result':{'turn':{'id':'v'}}})
        t.receive({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
        answer=t.receive({'id':77,'method':'item/tool/requestUserInput','params':{'threadId':'t','turnId':'v','itemId':'i','isBlocking':True,'questions':q}})
        self.assertEqual(claims[-1][0],'answer-1')
        self.assertEqual(answer,[{'id':77,'result':{'answers':{'choice':{'answers':['A']}}}}])
        t.receive({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'v','status':'completed'}}})
        self.assertTrue(t.done)
        self.assertRaises(ValueError,t.receive,{'id':77,'method':'item/tool/requestUserInput','params':{}})

    def test_mismatch_sends_no_answer(self):
        self.assertTrue(hasattr(n, 'Interactive'), 'interactive transport must reject mismatches')
        t=n.Interactive({'schema_version':'devforge.native-answer-policy/v1','steps':[]},b'x','/fixture',lambda *x:None,io.BytesIO())
        t.start();t.receive({'id':'init','result':{}});t.receive({'id':'thread','result':{'thread':{'id':'t'}}})
        self.assertRaises(ValueError,t.receive,{'id':2,'method':'item/tool/requestUserInput','params':{'threadId':'other','turnId':'v'}})

    def test_owned_transport_completes_and_reaps_fake_server(self):
        import time
        script = """import sys,json
r=lambda:json.loads(sys.stdin.readline())
w=lambda m:print(json.dumps(m),flush=True)
assert r()['method']=='initialize'
w({'id':'init','result':{}})
assert r()['method']=='initialized'
assert r()['method']=='thread/start'
w({'id':'thread','result':{'thread':{'id':'t'}}})
assert r()['method']=='turn/start'
w({'id':'turn-0','result':{'turn':{'id':'v'}}})
w({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
w({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'v','status':'completed'}}})
sys.stdin.read()
"""
        origin=time.monotonic(); claims=[]; transcript=io.BytesIO()
        adapter=n.Interactive({'schema_version':'devforge.native-answer-policy/v1','steps':[]},b'hello','/fixture',lambda unit,message:claims.append(unit),transcript)
        out,err=io.BytesIO(),io.BytesIO()
        result=n._collect([sys.executable,'-c',script],b'',2,lambda:time.monotonic()-origin,100000,out,err,interactive=adapter)
        self.assertEqual(result['status'],'EXITED', result)
        self.assertTrue(result['leader_reaped'])
        self.assertTrue(result['group_absent'])
        self.assertEqual(claims,['initial'])
        self.assertTrue(adapter.done)

    def test_failed_reservation_cannot_emit_generation_message(self):
        def denied(unit, message):
            raise OSError('durable reservation failed')
        t=n.Interactive({'schema_version':'devforge.native-answer-policy/v1','steps':[]}, b'x','/fixture',denied,io.BytesIO())
        t.start(); t.receive({'id':'init','result':{}})
        with self.assertRaisesRegex(OSError,'durable reservation failed'):
            t.receive({'id':'thread','result':{'thread':{'id':'t'}}})
        self.assertEqual(t.sent_prompts,[])

    def test_replay_rejects_transcript_claim_and_cross_attempt_mutations(self):
        import copy
        import tempfile
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch); prompt=root/'prompt'; prompt.write_bytes(b'initial')
            q=[{'id':'q','header':'Q','question':'Choose'}]
            policy={'schema_version':'devforge.native-answer-policy/v1','steps':[
                {'unit_id':'answer','action':'answer','questions':q,'answers':{'q':{'answers':['A']}}},
                {'unit_id':'next','action':'turn','text':'Continue'}]}
            binding={'attempt_id':'fixture'}; claims=[]; transcript=io.BytesIO(); server=[]
            def reserve(unit,message):
                path=root/(unit+'.json')
                n._new(path,n.canonical_json({'unit_id':unit,'binding':binding,'elapsed_seconds':1,'message':message}))
                claims.append({'path':str(path),'sha256':n.digest(path.read_bytes())})
            t=n.Interactive(policy,b'initial','/fixture',reserve,transcript)
            t.record('client',t.start())
            def feed(message):
                server.append(message); t.record('server',message)
                for out in t.receive(message): t.record('client',out)
            feed({'id':'init','result':{}})
            feed({'id':'thread','result':{'thread':{'id':'t'}}})
            feed({'id':'turn-0','result':{'turn':{'id':'v'}}})
            feed({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
            feed({'id':7,'method':'item/tool/requestUserInput','params':{'threadId':'t','turnId':'v','itemId':'i','isBlocking':True,'questions':q}})
            feed({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'v','status':'completed'}}})
            feed({'id':'turn-1','result':{'turn':{'id':'w'}}})
            feed({'method':'turn/started','params':{'threadId':'t','turn':{'id':'w'}}})
            feed({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'w','status':'completed'}}})
            request={'answer_policy':policy,'prompt':{'path':str(prompt),'sha256':n.digest(prompt.read_bytes())},'workspace':'/fixture','binding':binding,'reserved_at':0,'deadline':10}
            stdout=b''.join(n.canonical_json(m)+b'\n' for m in server)
            process={'stdout_complete':True,'output_limit_exceeded':False}
            raw=transcript.getvalue()
            self.assertEqual(n.observe_interactive(request,raw,stdout,claims,process)['status'],'OBSERVED')
            lines=raw.splitlines(keepends=True)
            variants=[b''.join(lines[:-1]), b''.join([lines[1],lines[0],*lines[2:]]), raw+lines[-1], b''.join(line for line in lines if b'"id":7,"result"' not in line)]
            for bad in variants:
                self.assertEqual(n.observe_interactive(request,bad,stdout,claims,process)['status'],'UNOBTAINABLE')
            for selected in (claims[:-1],claims+[claims[0]],[]):
                self.assertEqual(n.observe_interactive(request,raw,stdout,selected,process)['status'],'UNOBTAINABLE')
            wrong=copy.deepcopy(request);wrong['binding']={'attempt_id':'other'}
            self.assertEqual(n.observe_interactive(wrong,raw,stdout,claims,process)['status'],'UNOBTAINABLE')
            path=Path(claims[1]['path']); value=n._json(path.read_bytes());value['message']['result']['answers']['q']['answers']=['B']
            path.write_bytes(n.canonical_json(value))
            self.assertEqual(n.observe_interactive(request,raw,stdout,claims,process)['status'],'UNOBTAINABLE')
            changed=copy.deepcopy(claims);changed[1]['sha256']=n.digest(path.read_bytes())
            self.assertEqual(n.observe_interactive(request,raw,stdout,changed,process)['status'],'UNOBTAINABLE')

    def test_signal_shutdown_is_ineligible_without_complete_protocol_and_cleanup(self):
        import copy
        import utility_state
        body={'interactive':{},'events':{'status':'OBSERVED'},'freshness':{'status':'INTACT'},'process':{
            'status':'EXITED','exit_code':-15,'protocol_completed_before_owned_shutdown':True,
            'leader_reaped':True,'group_absent':True,'stdout_complete':True,'stderr_complete':True,'output_limit_exceeded':False}}
        self.assertTrue(utility_state._native_grade_eligible(body))
        for key in ('protocol_completed_before_owned_shutdown','leader_reaped','group_absent','stdout_complete','stderr_complete'):
            bad=copy.deepcopy(body);bad['process'][key]=False
            self.assertFalse(utility_state._native_grade_eligible(bad),key)
        for defect in ('events','interactive','overflow','status'):
            bad=copy.deepcopy(body)
            if defect=='events':bad['events']['status']='UNOBTAINABLE'
            elif defect=='interactive':bad.pop('interactive')
            elif defect=='overflow':bad['process']['output_limit_exceeded']=True
            else:bad['process']['status']='TIMED_OUT'
            self.assertFalse(utility_state._native_grade_eligible(bad),defect)

    def test_fake_server_failure_paths_reap_owned_process_without_completion(self):
        import time
        prefix="""import sys,json,time
r=lambda:json.loads(sys.stdin.readline())
w=lambda m:print(json.dumps(m),flush=True)
r();w({'id':'init','result':{}});r();r();w({'id':'thread','result':{'thread':{'id':'t'}}});r()
w({'id':'turn-0','result':{'turn':{'id':'v'}}})
w({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
"""
        tails=["time.sleep(2)","print('malformed',flush=True);time.sleep(2)","w({'id':7,'method':'item/tool/requestUserInput','params':{'threadId':'t','turnId':'v','itemId':'i','isBlocking':True,'questions':[]}});time.sleep(2)"]
        for tail in tails:
            with self.subTest(tail=tail):
                origin=time.monotonic()
                steps=[] if 'questions' not in tail else [{'unit_id':'answer','action':'answer','questions':[{'id':'expected','header':'Q','question':'Frozen question'}],'answers':{'expected':{'answers':['A']}}}]
                t=n.Interactive({'schema_version':'devforge.native-answer-policy/v1','steps':steps},b'initial','/fixture',lambda *a:None,io.BytesIO())
                out,err=io.BytesIO(),io.BytesIO()
                result=n._collect([sys.executable,'-c',prefix+tail],b'',0.3,lambda:time.monotonic()-origin,100000,out,err,interactive=t)
                self.assertNotEqual(result['status'],'EXITED',result)
                self.assertTrue(result['leader_reaped']);self.assertTrue(result['group_absent'])
                self.assertFalse(result.get('protocol_completed_before_owned_shutdown',False))
                self.assertEqual(t.sent_prompts,['initial'])

    def test_fake_server_cannot_continue_after_durable_answer_claim_failure(self):
        import time
        script="""import sys,json,time
r=lambda:json.loads(sys.stdin.readline())
w=lambda m:print(json.dumps(m),flush=True)
r();w({'id':'init','result':{}});r();r();w({'id':'thread','result':{'thread':{'id':'t'}}});r()
w({'id':'turn-0','result':{'turn':{'id':'v'}}})
w({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
w({'id':7,'method':'item/tool/requestUserInput','params':{'threadId':'t','turnId':'v','itemId':'i','isBlocking':True,'questions':[{'id':'q','header':'Q','question':'Choose'}]}})
answer=r()
print('FORBIDDEN_ANSWER_DELIVERED',flush=True)
"""
        q=[{'id':'q','header':'Q','question':'Choose'}]
        policy={'schema_version':'devforge.native-answer-policy/v1','steps':[{'unit_id':'answer','action':'answer','questions':q,'answers':{'q':{'answers':['A']}}}]}
        claimed=[]
        def reserve(unit,message):
            if unit=='answer':raise OSError('fsync refused')
            claimed.append(unit)
        transcript=io.BytesIO();t=n.Interactive(policy,b'initial','/fixture',reserve,transcript)
        out,err=io.BytesIO(),io.BytesIO();origin=time.monotonic()
        result=n._collect([sys.executable,'-c',script],b'',1,lambda:time.monotonic()-origin,100000,out,err,interactive=t)
        self.assertEqual(claimed,['initial'])
        self.assertNotIn(b'FORBIDDEN_ANSWER_DELIVERED',out.getvalue())
        self.assertNotIn(b'"id":7,"result"',transcript.getvalue())
        self.assertEqual(result['status'],'COULD_NOT_RUN',result)
        self.assertTrue(result['leader_reaped']);self.assertTrue(result['group_absent'])

class OperatorPolicyTests(unittest.TestCase):
    def test_unknown_question_requires_exact_bound_operator_selection(self):
        import tempfile
        policy={'schema_version':'devforge.native-answer-policy/v2','steps':[{'unit_id':'facts','action':'operator_answer','allowed_answers':{'repo':'Use repository A','unknown':'Not decided'}}]}
        accepted=True
        try:n.answer_policy(policy)
        except ValueError:accepted=False
        self.assertTrue(accepted,'v2 must support bounded operator facts for previously unknown questions')
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch); root.chmod(0o700)
            binding={'attempt_id':'fixture'}; claims=[]
            inbox=n.OperatorInbox(root,binding,policy)
            t=n.Interactive(policy,b'initial','/fixture',lambda u,m:claims.append((u,m)),io.BytesIO(),operator=inbox)
            t.start();t.receive({'id':'init','result':{}});t.receive({'id':'thread','result':{'thread':{'id':'t'}}})
            t.receive({'id':'turn-0','result':{'turn':{'id':'v'}}});t.receive({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
            request={'id':41,'method':'item/tool/requestUserInput','params':{'threadId':'t','turnId':'v','itemId':'i','isBlocking':True,'questions':[{'id':'unpredicted-id','header':'Repository','question':'Which repository should this new task use?'}]}}
            self.assertEqual(t.receive(request),[])
            self.assertEqual(t.poll(),[])
            self.assertEqual([u for u,m in claims],['initial'])
            pending=root/'operator-facts-request.json'
            self.assertTrue(pending.exists())
            n.write_operator_decision(pending,{'unpredicted-id':'repo'},relevance_confirmed=True)
            answer=t.poll()
            self.assertEqual(answer,[{'id':41,'result':{'answers':{'unpredicted-id':{'answers':['Use repository A']}}}}])
            self.assertEqual([u for u,m in claims],['initial','facts'])
            with self.assertRaises(FileExistsError):
                n.write_operator_decision(pending,{'unpredicted-id':'unknown'},relevance_confirmed=True)

    def test_stale_unit_coverage_key_and_relevance_decisions_are_rejected(self):
        import copy,tempfile
        policy={'schema_version':'devforge.native-answer-policy/v2','steps':[{'unit_id':'facts','action':'operator_answer','allowed_answers':{'fact':'Frozen fact'}}]}
        message={'id':1,'method':'item/tool/requestUserInput','params':{'questions':[{'id':'q','question':'Unknown question'}]}}
        for defect in ('stale','unit','binding','missing','extra','key','relevance','symlink','collision'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as scratch:
                root=Path(scratch);inbox=n.OperatorInbox(root,{'attempt_id':'one'},policy)
                inbox.publish(policy['steps'][0],message)
                pending=n._json((root/'operator-facts-request.json').read_bytes())
                decision={'schema_version':'devforge.native-operator-decision/v1',**{k:pending[k] for k in ('unit_id','binding','policy_sha256','request_sha256')},'relevance_confirmed':True,'selections':{'q':'fact'}}
                if defect=='stale':decision['request_sha256']='0'*64
                elif defect=='unit':decision['unit_id']='other'
                elif defect=='binding':decision['binding']={'attempt_id':'two'}
                elif defect=='missing':decision['selections']={}
                elif defect=='extra':decision['selections']['extra']='fact'
                elif defect=='key':decision['selections']['q']='invented'
                elif defect=='relevance':decision['relevance_confirmed']=False
                target=root/'operator-facts-decision.json'
                if defect=='symlink':target.symlink_to(root/'missing')
                else:target.write_bytes(n.canonical_json(decision))
                if defect=='collision':(root/'operator-facts-accepted.json').write_bytes(b'occupied')
                with self.assertRaises((ValueError,OSError)):inbox.poll()
                self.assertIsNotNone(inbox.current)

    def test_operator_turn_uses_captured_question_and_bound_decision_in_replay(self):
        import tempfile,copy
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch);prompt=root/'prompt';prompt.write_bytes(b'initial')
            policy={'schema_version':'devforge.native-answer-policy/v2','steps':[{'unit_id':'followup','action':'operator_turn','allowed_answers':{'fact':'Use the existing repository'}}]}
            binding={'attempt_id':'fixture'};inbox=n.OperatorInbox(root,binding,policy);claims=[];transcript=io.BytesIO();server=[]
            def reserve(unit,message):
                path=root/('unit-'+unit+'.json');n._new(path,n.canonical_json({'unit_id':unit,'binding':binding,'elapsed_seconds':1,'message':message}));claims.append({'path':str(path),'sha256':n.digest(path.read_bytes())})
            t=n.Interactive(policy,b'initial','/fixture',reserve,transcript,operator=inbox);t.record('client',t.start())
            def feed(message):
                server.append(message);t.record('server',message)
                for out in t.receive(message):t.record('client',out)
            feed({'id':'init','result':{}});feed({'id':'thread','result':{'thread':{'id':'t'}}})
            feed({'id':'turn-0','result':{'turn':{'id':'v'}}});feed({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
            feed({'method':'item/completed','params':{'threadId':'t','turnId':'v','item':{'type':'agentMessage','id':'a','text':'Which repository should I use?'}}})
            feed({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'v','status':'completed'}}})
            pending=root/'operator-followup-request.json';self.assertIn(b'Which repository',pending.read_bytes());self.assertEqual(len(claims),1)
            n.write_operator_decision(pending,{'next_turn':'fact'},relevance_confirmed=True)
            for out in t.poll():
                self.assertEqual(out['method'],'turn/start');self.assertEqual(out['params']['input'][0]['text'],'Use the existing repository');t.record('client',out)
            feed({'id':'turn-1','result':{'turn':{'id':'w'}}});feed({'method':'turn/started','params':{'threadId':'t','turn':{'id':'w'}}});feed({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'w','status':'completed'}}})
            request={'answer_policy':policy,'prompt':{'path':str(prompt),'sha256':n.digest(prompt.read_bytes())},'workspace':'/fixture','binding':binding,'reserved_at':0,'deadline':10}
            stdout=b''.join(n.canonical_json(m)+b'\n' for m in server);process={'stdout_complete':True,'output_limit_exceeded':False}
            self.assertEqual(n.observe_interactive(request,transcript.getvalue(),stdout,claims,process,operators=inbox.records)['status'],'OBSERVED')
            self.assertEqual(n.observe_interactive(request,transcript.getvalue(),stdout,claims,process)['status'],'UNOBTAINABLE')
            path=Path(inbox.records[0]['decision']['path']);value=n._json(path.read_bytes());value['selections']['next_turn']='invented';path.write_bytes(n.canonical_json(value))
            self.assertEqual(n.observe_interactive(request,transcript.getvalue(),stdout,claims,process,operators=inbox.records)['status'],'UNOBTAINABLE')

    def test_unanswered_operator_deadline_reaps_actual_fake_process(self):
        import tempfile,time
        script="""import sys,json,time
r=lambda:json.loads(sys.stdin.readline())
w=lambda m:print(json.dumps(m),flush=True)
r();w({'id':'init','result':{}});r();r();w({'id':'thread','result':{'thread':{'id':'t'}}});r()
w({'id':'turn-0','result':{'turn':{'id':'v'}}});w({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
w({'id':7,'method':'item/tool/requestUserInput','params':{'threadId':'t','turnId':'v','itemId':'i','isBlocking':True,'questions':[{'id':'new','question':'Unknown wording','header':'Q'}]}})
sys.stdin.read()
"""
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch);policy={'schema_version':'devforge.native-answer-policy/v2','steps':[{'unit_id':'facts','action':'operator_answer','allowed_answers':{'fact':'Frozen fact'}}]}
            inbox=n.OperatorInbox(root,{'attempt_id':'fixture'},policy);claims=[];t=n.Interactive(policy,b'initial','/fixture',lambda u,m:claims.append(u),io.BytesIO(),operator=inbox)
            origin=time.monotonic();out,err=io.BytesIO(),io.BytesIO()
            result=n._collect([sys.executable,'-c',script],b'',0.3,lambda:time.monotonic()-origin,100000,out,err,interactive=t)
            self.assertEqual(result['status'],'TIMED_OUT',result);self.assertTrue(result['leader_reaped']);self.assertTrue(result['group_absent'])
            self.assertEqual(claims,['initial']);self.assertTrue((root/'operator-facts-request.json').exists());self.assertFalse((root/'operator-facts-accepted.json').exists())

    def test_actual_fake_transport_delivers_only_owner_selected_frozen_fact(self):
        import tempfile,time
        script="""import sys,json
r=lambda:json.loads(sys.stdin.readline())
w=lambda m:print(json.dumps(m),flush=True)
r();w({'id':'init','result':{}});r();r();w({'id':'thread','result':{'thread':{'id':'t'}}});r()
w({'id':'turn-0','result':{'turn':{'id':'v'}}});w({'method':'turn/started','params':{'threadId':'t','turn':{'id':'v'}}})
w({'id':7,'method':'item/tool/requestUserInput','params':{'threadId':'t','turnId':'v','itemId':'i','isBlocking':True,'questions':[{'id':'new','question':'Unknown wording','header':'Q'}]}})
assert r()=={'id':7,'result':{'answers':{'new':{'answers':['Frozen fact']}}}}
w({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'v','status':'completed'}}})
sys.stdin.read()
"""
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch);policy={'schema_version':'devforge.native-answer-policy/v2','steps':[{'unit_id':'facts','action':'operator_answer','allowed_answers':{'fact':'Frozen fact'}}]}
            inbox=n.OperatorInbox(root,{'attempt_id':'fixture'},policy);claims=[];t=n.Interactive(policy,b'initial','/fixture',lambda u,m:claims.append(u),io.BytesIO(),operator=inbox)
            origin=time.monotonic();out,err=io.BytesIO(),io.BytesIO();selected=[]
            def clock():
                pending=root/'operator-facts-request.json'
                if pending.exists() and not selected:
                    n.write_operator_decision(pending,{'new':'fact'},relevance_confirmed=True);selected.append(True)
                return time.monotonic()-origin
            result=n._collect([sys.executable,'-c',script],b'',1,clock,100000,out,err,interactive=t)
            self.assertEqual(result['status'],'EXITED',result);self.assertTrue(result['leader_reaped']);self.assertTrue(result['group_absent']);self.assertEqual(claims,['initial','facts'])


class OperatorDocumentBoundaryTests(unittest.TestCase):
    def fixture(self):
        step = {'unit_id': 'bounded', 'action': 'operator_answer', 'allowed_answers': {'selected': 'A'}}
        policy = {'schema_version': 'devforge.native-answer-policy/v2', 'steps': [step]}
        message = {'id': 7, 'method': 'item/tool/requestUserInput', 'params': {'threadId': 't', 'turnId': 'v', 'itemId': 'i', 'isBlocking': True, 'questions': [{'id': 'q', 'question': 'x'}]}}
        return step, policy, message

    def test_wire_limit_cannot_publish_unreadable_wrapped_request(self):
        import tempfile
        step, policy, message = self.fixture()
        message['params']['questions'][0]['question'] += 'x' * (n.LIMIT - len(n.canonical_json(message)))
        self.assertEqual(len(n.canonical_json(message)), 8 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            inbox = n.OperatorInbox(root, {'attempt_id': 'fixture'}, policy)
            with self.assertRaisesRegex(ValueError, 'Operator request document exceeds bound'):
                inbox.publish(step, message)
            self.assertEqual(list(root.iterdir()), [])
            self.assertIsNone(inbox.current)
            self.assertEqual(inbox.records, [])

    def test_exact_document_limit_is_readable_and_replayable(self):
        import tempfile
        step, policy, message = self.fixture()
        binding = {'attempt_id': 'fixture'}
        envelope = {'schema_version': 'devforge.native-operator-request/v1', 'binding': binding, 'unit_id': 'bounded', 'policy_sha256': '0' * 64, 'request_sha256': '0' * 64, 'request': message, 'question_ids': ['q'], 'allowed_answers': {'selected': 'A'}}
        message['params']['questions'][0]['question'] += 'x' * (8 * 1024 * 1024 - len(n.canonical_json(envelope)))
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            inbox = n.OperatorInbox(root, binding, policy)
            inbox.publish(step, message)
            pending = root / 'operator-bounded-request.json'
            self.assertEqual(pending.stat().st_size, 8 * 1024 * 1024)
            n.write_operator_decision(pending, {'q': 'selected'}, relevance_confirmed=True)
            self.assertEqual(inbox.poll(), {'q': {'answers': ['A']}})
            replay = n.OperatorInbox(root, binding, policy, replay=inbox.records)
            replay.publish(step, message)
            self.assertEqual(replay.poll(), {'q': {'answers': ['A']}})
