"""Offline regression for per-paper context ownership and handoff."""
import contextlib
import json
import queue
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import codex_models
import codex_bridge
import paper_ai
RealCodexSession=paper_ai.CodexSession

PID='0123456789abcdef'


def resolve(model='',reasoning_effort=''):
    if model and model not in ('model-a','model-b'):
        raise ValueError('unavailable model')
    return {'model':model or 'model-a','reasoning_effort':reasoning_effort or 'medium'}


class FakeSession:
    next_id=0
    calls=[]
    def __init__(self,*_):
        self.temp=type('Temp',(),{'name':str(ROOT/'work')})()
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def rpc(self,method,params):
        self.calls.append((method,params))
        if method=='thread/start':
            FakeSession.next_id+=1
            return {'thread':{'id':f'00000000-0000-0000-0000-{FakeSession.next_id:012d}'}}
        return {}
    def turn(self,thread_id,prompt,config,schema,**_):
        self.calls.append(('turn',{'thread_id':thread_id,'prompt':prompt,'config':config}))
        if 'answer' in schema['properties']:
            return {'answer':'实验证据见所引段落。','citations':['p1-b1']}
        return {key:f'译文 {key}' for key in schema['required']}


class PaperAITests(unittest.TestCase):
    def setUp(self):
        work=ROOT/'work';work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='paper-ai-',dir=work)
        self.root=Path(self.temp.name)
        self.library=self.root/'library';(self.library/PID).mkdir(parents=True)
        self.path=self.root/'library.sqlite3'
        with self.db() as con:
            con.execute('CREATE TABLE papers(id TEXT PRIMARY KEY)')
            con.execute('INSERT INTO papers VALUES(?)',(PID,))
            paper_ai.init(con)
        self.doc={'id':PID,'title':'Test paper','notes':'Measure force carefully.',
                  'blocks':[{'id':'p1-b1','page':1,'text':'We measured contact forces at 10 Hz.'}],
                  'translation':{'status':'idle'}}
        self.model_patch=patch.object(codex_models,'resolve_config',side_effect=resolve)
        self.session_patch=patch.object(paper_ai,'CodexSession',FakeSession)
        self.model_patch.start();self.session_patch.start()
        FakeSession.calls=[];FakeSession.next_id=0

    def tearDown(self):
        self.session_patch.stop();self.model_patch.stop();self.temp.cleanup()

    @contextlib.contextmanager
    def db(self):
        con=sqlite3.connect(self.path)
        try:
            with con: yield con
        finally: con.close()

    def test_ask_reuses_thread_and_preserves_history_across_model_switch(self):
        first=paper_ai.ask(self.db,PID,self.doc,'What was measured?')
        second=paper_ai.ask(self.db,PID,self.doc,'At what rate?')
        self.assertEqual(first['thread_id'],second['thread_id'])
        self.assertEqual(len([c for c in FakeSession.calls if c[0]=='thread/start']),1)
        with self.db() as con:
            old=paper_ai.history(con,PID)
            self.assertEqual(len(old['messages']),4)
            result=paper_ai.switch_model(con,PID,{'model':'model-b'},self.doc,self.library)
            self.assertEqual(result['generation'],2)
        handoff=json.loads((self.library/PID/'ai-handoff-2.json').read_text(encoding='utf-8'))
        self.assertEqual(handoff['user_notes'],'Measure force carefully.')
        self.assertEqual(handoff['verified_in_paper'][0]['citations'][0]['block_id'],'p1-b1')
        third=paper_ai.ask(self.db,PID,self.doc,'Does it report contact force?')
        self.assertNotEqual(third['thread_id'],first['thread_id'])
        with self.db() as con:
            self.assertEqual(len(paper_ai.history(con,PID,1)['messages']),4)
            self.assertEqual(len(paper_ai.history(con,PID)['messages']),2)

    def test_translation_thread_is_separate_and_selection_is_stateless(self):
        config={'model':'model-a','reasoning_effort':'medium'}
        saved=[]
        tid,result=paper_ai.translate_batch(self.db,PID,self.doc,self.doc['blocks'],config,None,
                                              on_thread=saved.append)
        self.assertEqual(saved,[tid])
        tid2,_=paper_ai.translate_batch(self.db,PID,self.doc,self.doc['blocks'],config,tid)
        self.assertEqual(tid,tid2)
        self.assertEqual(result,{'p1-b1':'译文 p1-b1'})
        with self.db() as con:
            self.assertFalse(paper_ai.history(con,PID)['messages'])
        with patch('selection_codex.translate',return_value='接触力'):
            self.assertEqual(codex_bridge.translate_selection('contact force',**config),'接触力')

    def test_invalid_citation_is_not_saved(self):
        original=FakeSession.turn
        def bad(self,thread_id,prompt,config,schema,**kwargs):
            return {'answer':'不实引用','citations':['another-paper']}
        FakeSession.turn=bad
        try:
            with self.assertRaises(paper_ai.SessionError):
                paper_ai.ask(self.db,PID,self.doc,'What was measured?')
        finally:
            FakeSession.turn=original
        with self.db() as con:
            self.assertEqual(paper_ai.history(con,PID)['messages'],[])

    def test_app_server_completed_turn_payload_is_parsed(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {}
        session.events.put({'method':'turn/completed','params':{'threadId':'test-thread',
            'turn':{'status':'completed','items':[{'type':'agentMessage',
                'text':'{"answer":"结论","citations":["p1-b1"]}'}]}}})
        answer=RealCodexSession.turn(session,'test-thread','question',
            {'model':'model-a','reasoning_effort':'medium'},paper_ai.ANSWER_SCHEMA)
        self.assertEqual(answer['citations'],['p1-b1'])

    def test_app_server_error_notification_is_reported(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'test-turn'}}
        session.events.put({'method':'error','params':{'threadId':'test-thread',
            'turnId':'test-turn','willRetry':False,
            'error':{'message':'rate limit reached'}}})
        with self.assertRaises(paper_ai.SessionError) as raised:
            RealCodexSession.turn(session,'test-thread','question',
                {'model':'model-a','reasoning_effort':'medium'},paper_ai.ANSWER_SCHEMA)
        self.assertEqual(raised.exception.code,'quota')

    def test_app_server_failed_turn_is_reported(self):
        session=RealCodexSession.__new__(RealCodexSession)
        session.deadline=time.monotonic()+1
        session.events=queue.Queue()
        session.rpc=lambda *_: {'turn':{'id':'test-turn'}}
        session.events.put({'method':'turn/completed','params':{'threadId':'test-thread',
            'turn':{'id':'test-turn','status':'failed',
                    'error':{'message':'connection timed out'},'items':[]}}})
        with self.assertRaises(paper_ai.SessionError) as raised:
            RealCodexSession.turn(session,'test-thread','question',
                {'model':'model-a','reasoning_effort':'medium'},paper_ai.ANSWER_SCHEMA)
        self.assertEqual(raised.exception.code,'network')


if __name__=='__main__': unittest.main()
