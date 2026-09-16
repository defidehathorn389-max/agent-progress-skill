import unittest,tempfile,sys,json,copy,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import handoff as h

def state():
 return dict(title='Test project',status='IN_PROGRESS',goal='Recover verified progress',constraints=[],completed=[],pending=[],decisions=[],artifacts=[],evidence=[],risks=[],next_actions=[],avoid=[],sync={'memory':'LOCAL_ONLY'},handoff={'first_action':'Read evidence','running_operations':[]},related_projects=[])

class Tests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.r=Path(self.t.name);self.p='test/example'
 def tearDown(self):self.t.cleanup()
 def add(self,s=None,expected='NEW',merge=None):return h.checkpoint(self.r,self.p,s or state(),expected,'Test checkpoint',merge)
 def test_create_read_and_revision(self):
  a=self.add();b=self.add(expected=a['checkpoint_id']);self.assertEqual(b['revision'],2);self.assertEqual(h.validate(self.r)['projects'][0]['checkpoints_checked'],2)
 def test_new_view_is_canonical(self):
  s=state();s['handoff']={'running_operations':[],'last_actor':'test','first_action':'read'};s['pending']=[{'z':'last','a':'first'}];self.add(s);self.assertFalse(h.validate(self.r)['projects'][0]['warnings'])
 def test_stale_head_rejected(self):
  a=self.add();self.add(expected=a['checkpoint_id'])
  with self.assertRaises(h.HandoffError):self.add(expected=a['checkpoint_id'])
 def test_lock_not_stolen(self):
  a=self.add();p=h.project_dir(self.r,self.p);(p/'.write.lock').write_text('{}')
  with self.assertRaises(h.HandoffError):self.add(expected=a['checkpoint_id'])
  self.assertTrue((p/'.write.lock').exists())
 def test_tamper_detected(self):
  a=self.add();p=h.project_dir(self.r,self.p)/'checkpoints'/(a['checkpoint_id']+'.json');p.write_bytes(p.read_bytes()+b' ')
  with self.assertRaises(h.HandoffError):h.validate(self.r)
 def test_parent_tamper_detected(self):
  a=self.add();self.add(expected=a['checkpoint_id']);p=h.project_dir(self.r,self.p)/'checkpoints'/(a['checkpoint_id']+'.json');p.write_bytes(p.read_bytes()+b' ')
  with self.assertRaises(h.HandoffError):h.validate(self.r)
 def test_view_rebuild(self):
  self.add();p=h.project_dir(self.r,self.p);(p/'CURRENT.md').write_text('stale');self.assertTrue(h.validate(self.r)['projects'][0]['warnings']);h.rebuild(self.r);self.assertFalse(h.validate(self.r)['projects'][0]['warnings'])
 def test_secret_field_and_pattern_rejected(self):
  s=state();s['handoff']['password']='example'
  with self.assertRaises(h.HandoffError):self.add(s)
  s=state();s['goal']='gh'+'p_'+'A'*36
  with self.assertRaises(h.HandoffError):self.add(s)
 def test_evidence_required(self):
  s=state();s['completed']=[{'item':'Done','evidence':['missing']}]
  with self.assertRaises(h.HandoffError):self.add(s)
 def test_direction_isolation_and_path_traversal(self):
  self.add();h.checkpoint(self.r,'another/example',state(),'NEW','Different direction');self.assertEqual(len(h.validate(self.r)['projects']),2)
  with self.assertRaises(h.HandoffError):h.project_dir(self.r,'../example')
 def test_local_artifact_hash(self):
  f=self.r/'proof.txt';f.write_text('proof');s=state();s['artifacts']=[{'id':'proof','role':'test','local_path':'proof.txt','availability':'LOCAL','sha256':h.digest(f.read_bytes())}];self.add(s);h.validate(self.r,workspace=self.r);f.write_text('changed')
  with self.assertRaises(h.HandoffError):h.validate(self.r,workspace=self.r)
 def test_orphan_and_merge(self):
  a=self.add();p=h.project_dir(self.r,self.p);base=(p/'HEAD.json').read_bytes();b=self.add(expected=a['checkpoint_id']);h.atomic(p/'HEAD.json',base);c=self.add(expected=a['checkpoint_id'])
  self.assertTrue(h.validate(self.r)['projects'][0]['warnings'])
  d=self.add(expected=c['checkpoint_id'],merge=b['checkpoint_id']);self.assertEqual(d['revision'],3);self.assertEqual(h.validate(self.r)['projects'][0]['checkpoints_checked'],4)
 def test_cross_project_parent_rejected(self):
  a=self.add();b=h.checkpoint(self.r,'another/example',state(),'NEW','Other project');src=h.project_dir(self.r,'another/example')/'checkpoints'/(b['checkpoint_id']+'.json');dst=h.project_dir(self.r,self.p)/'checkpoints'/src.name;dst.write_bytes(src.read_bytes())
  with self.assertRaises(h.HandoffError):self.add(expected=a['checkpoint_id'],merge=b['checkpoint_id'])
 def test_secret_in_note_rejected(self):
  with self.assertRaises(h.HandoffError):h.checkpoint(self.r,self.p,state(),'NEW','Bearer '+'A'*30)
 def test_symlink_escape_rejected(self):
  (self.r/'projects').mkdir();(self.r/'projects'/'escape').symlink_to(self.r.parent,target_is_directory=True)
  with self.assertRaises(h.HandoffError):h.project_dir(self.r,'escape/project')

class ExportTests(unittest.TestCase):
 def test_package_manifest_and_credential_exclusion(self):
  from export_handoff import export
  import zipfile
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);(r/'notes').mkdir();(r/'notes/a.md').write_text('progress');(r/'notes/credentials').mkdir();(r/'notes/credentials/private.enc.json').write_text('{}');(r/'notes/movie.mp4').write_bytes(b'media');z=r/'portable.zip';result=export(r,['notes'],z)
   with zipfile.ZipFile(z) as a:
    self.assertIn('notes/a.md',a.namelist());self.assertNotIn('notes/credentials/private.enc.json',a.namelist());self.assertNotIn('notes/movie.mp4',a.namelist());m=json.loads(a.read('PACKAGE_MANIFEST.json'));self.assertEqual(h.digest(a.read('notes/a.md')),m['files']['notes/a.md'])
 def test_package_secret_rejected(self):
  from export_handoff import export
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);(r/'bad.md').write_text('gh'+'p_'+'A'*36)
   with self.assertRaises(h.HandoffError):export(r,['bad.md'],r/'bad.zip')
 def test_package_include_escape_rejected(self):
  from export_handoff import export
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(h.HandoffError):export(Path(t),['../outside'],Path(t)/'bad.zip')

if __name__=='__main__':unittest.main()
