import unittest,tempfile,sys,shutil,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import handoff
class PartialIndex(unittest.TestCase):
 def test_partial_checkpoint_keeps_other_project_navigation(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);template=json.loads((Path(__file__).resolve().parents[1]/'templates/state.json').read_text());template.update(title='Example',goal='Test',status='IN_PROGRESS')
   a=handoff.checkpoint(root,'video/first',template,'NEW','create')
   handoff.checkpoint(root,'research/other',template,'NEW','create')
   shutil.rmtree(root/'projects/research/other')
   handoff.checkpoint(root,'video/first',template,a['checkpoint_id'],'update loaded project')
   self.assertIn('research/other',(root/'INDEX.md').read_text())
   result=handoff.validate(root,'video/first')
   self.assertEqual(result['unloaded_projects'],['research/other'])
   self.assertTrue(result['index_current'])
if __name__=='__main__':unittest.main()
