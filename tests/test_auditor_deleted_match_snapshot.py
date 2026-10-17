"""Offline regression using the exact method and a real SQLite ORM expiration.
No application import or external calls. Helpers/model confirmation are synthetic.
"""
import ast
import logging
import sys
import types
import unittest
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine,Column,Integer,String,Float
from sqlalchemy.orm import declarative_base,sessionmaker
from sqlalchemy.orm.exc import ObjectDeletedError

Base=declarative_base()
class VLog(Base):
    __tablename__='vlog'
    id=Column(Integer,primary_key=True)
    bullhorn_candidate_id=Column(Integer)
    candidate_name=Column(String)
    applied_job_id=Column(Integer)
    applied_job_title=Column(String)
    resume_text=Column(String)
    highest_match_score=Column(Float)
class Match(Base):
    __tablename__='match'
    id=Column(Integer,primary_key=True)
    vetting_log_id=Column(Integer)
    is_applied_job=Column(Integer)
    bullhorn_job_id=Column(Integer)
    job_title=Column(String)
    match_score=Column(Float)
class Audit:
    def __init__(self,**kw):self.__dict__.update(kw);self.id=123
class Query:
    def __get__(self,instance,owner):return SESSION.query(owner)
Match.query=Query()
SESSION=None

def load_method(source):
    tree=ast.parse(source.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OrchestrationMixin')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_process_candidate_audit')
    ns={'logger':logging.getLogger('offline-audit-regression'),'Dict':dict}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),ns)
    return ns[method.name]

@contextmanager
def imports(session):
    old={k:sys.modules.get(k) for k in ['app','models']}
    app=types.ModuleType('app');app.db=types.SimpleNamespace(session=session)
    models=types.ModuleType('models');models.CandidateJobMatch=Match;models.VettingAuditLog=Audit
    sys.modules.update(app=app,models=models)
    try:yield
    finally:
        for k,v in old.items():
            if v is None:sys.modules.pop(k,None)
            else:sys.modules[k]=v

class AuditRegression(unittest.TestCase):
    source=Path(__file__).resolve().parents[1]/'vetting_audit_service/orchestration_mixin.py'
    def setUp(self):
        global SESSION
        self.engine=create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.session=SESSION=sessionmaker(bind=self.engine,expire_on_commit=True)()
        self.log=VLog(id=1,bullhorn_candidate_id=42,candidate_name='Synthetic',applied_job_id=7,applied_job_title='Synthetic job',resume_text='Synthetic resume only',highest_match_score=15)
        self.match=Match(id=2,vetting_log_id=1,is_applied_job=True,bullhorn_job_id=7,job_title='Synthetic job',match_score=15)
        self.session.add_all([self.log,self.match]);self.session.commit()
        self.saved=[];self.reset_calls=0;self.issues=['synthetic'];self.finding={'finding_type':'employment_gap_misfire','confidence':'high','reasoning':'synthetic'}
        self.cooldown=None;self.cap=None;self.accept=True
        parent=self
        class Service:
            _process_candidate_audit=load_method(parent.source)
            def _check_audit_cooldown(self,*a):return parent.cooldown
            def _run_false_positive_checks(self,*a):return parent.issues
            def _run_heuristic_checks(self,*a):return parent.issues
            def _run_ai_audit(self,*a,**kw):return parent.finding
            def _check_pre_cutoff_eligibility(self,*a):return None
            def _check_revet_caps_and_stability(self,*a):return parent.cap
            def _commit_audit_log(self,row):
                if parent.accept:parent.saved.append(row)
                return parent.accept
            def _trigger_revet(self,*a):
                parent.reset_calls+=1
                parent.session.query(Match).delete(synchronize_session=False)
                parent.session.query(VLog).delete(synchronize_session=False)
                parent.session.commit()
                return None
        self.service=Service()
    def tearDown(self):self.session.close();self.engine.dispose()
    def run_audit(self,mode='not_qualified'):
        summary=dict(total_audited=0,issues_found=0,revets_triggered=0,revets_skipped_capped=0,revets_skipped_stable=0,revets_skipped_pre_cutoff=0,qualified_audited=0,qualified_issues_found=0,details=[])
        with imports(self.session):self.service._process_candidate_audit(self.log,mode=mode,summary=summary)
        return summary
    def test_orm_delete_commit_really_expires_retained_match(self):
        self.service._trigger_revet(42,1)
        with self.assertRaises(ObjectDeletedError):_ = self.match.match_score
    def test_reset_keeps_success_summary_and_no_error_audit(self):
        summary=self.run_audit()
        self.assertEqual(summary['total_audited'],1)
        self.assertEqual(summary['revets_triggered'],1)
        self.assertEqual(summary['details'][0]['job_id'],7)
        self.assertEqual(summary['details'][0]['job_title'],'Synthetic job')
        self.assertEqual(summary['details'][0]['original_score'],15)
        self.assertEqual(len(self.saved),1)
        self.assertEqual(self.saved[0].action_taken,'revet_triggered')
        self.assertEqual(self.session.query(Match).count(),0)
    def test_false_positive_reset_preserves_summary(self):
        result=self.run_audit('qualified_false_positive')
        self.assertEqual(result['qualified_audited'],1)
        self.assertEqual(result['qualified_issues_found'],1)
    def test_no_issue_no_reset(self):
        self.issues=[];result=self.run_audit()
        self.assertEqual(result['total_audited'],1);self.assertEqual(self.reset_calls,0)
    def test_medium_confidence_flags_without_reset(self):
        self.finding['confidence']='medium';result=self.run_audit()
        self.assertEqual(result['details'][0]['action_taken'],'flagged_for_review');self.assertEqual(self.reset_calls,0)
    def test_job_mismatch_still_suppresses_reset(self):
        self.match.bullhorn_job_id=8;self.session.commit();result=self.run_audit()
        self.assertEqual(result['details'][0]['action_taken'],'revet_skipped_job_mismatch');self.assertEqual(self.reset_calls,0)
    def test_capped_case_unchanged(self):
        self.cap=('revet_skipped_capped','synthetic cap');result=self.run_audit()
        self.assertEqual(result['revets_skipped_capped'],1);self.assertEqual(self.reset_calls,0)
    def test_concurrent_duplicate_audit_loser_does_not_reset(self):
        self.accept=False;result=self.run_audit()
        self.assertEqual(result['total_audited'],0);self.assertEqual(self.reset_calls,0)
    def test_missing_match_keeps_no_action(self):
        self.session.query(Match).delete();self.session.commit();result=self.run_audit()
        self.assertEqual(result['total_audited'],1);self.assertEqual(self.saved[0].action_taken,'no_action')

if __name__=='__main__':
    unittest.main(verbosity=2)
