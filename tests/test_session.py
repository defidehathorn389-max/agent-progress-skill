import unittest,tempfile,sys,subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from session_github import SessionGitHub

class SessionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.u=patch('session_github.unlock',return_value='synthetic-test-no-access');self.r=patch('session_github.read_envelope',return_value={});self.u.start();self.r.start();self.s=SessionGitHub('unused-test-input','not-a-real-vault',self.tmp.name,['owner/allowed'])
 def tearDown(self):self.s.close();self.u.stop();self.r.stop();self.tmp.cleanup()
 def test_auth_reusable_no_repeat_prompt(self):
  for _ in range(2):
   p=subprocess.run([str(self.s.temp/'askpass.py'),"Password for 'https://x-access-token@github.com':"],env=self.s.env,stdout=subprocess.PIPE,check=True);self.assertEqual(p.stdout.strip(),b'synthetic-test-no-access')
 def test_reject_other_host(self):
  p=subprocess.run([str(self.s.temp/'askpass.py'),"Password for 'https://github.com.evil.example':"],env=self.s.env,stdout=subprocess.PIPE);self.assertNotEqual(p.returncode,0);self.assertEqual(p.stdout,b'')
 def test_reject_other_repository(self):
  with self.assertRaises(RuntimeError):self.s.api('GET','repos/owner/other')
  with self.assertRaises(RuntimeError):self.s.git(['clone','https://github.com/owner/other.git','unused'])
 def test_no_plaintext_credential_file(self):
  for p in self.s.temp.iterdir():
   if p.is_file():self.assertNotIn(b'synthetic-test-no-access',p.read_bytes())

if __name__=='__main__':unittest.main()
