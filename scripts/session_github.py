"""In-memory GitHub authentication for an explicitly authorized conversation.
Caller supplies the current conversation's passphrase; no extra prompt per operation.
Needs requests and cryptography. This is not a cross-conversation credential service.
"""
from pathlib import Path
import os,socket,threading,tempfile,subprocess,requests,shutil
from credential_vault import unlock,read_envelope

class SessionGitHub:
 def __init__(self,passphrase,vault_path,workspace_cache,approved_repositories):
  self.token=unlock(read_envelope(vault_path),passphrase)
  self.approved=set(approved_repositories);self.secret_values=(self.token,passphrase)
  self.http=requests.Session();self.http.headers.update(Authorization='Bearer '+self.token,Accept='application/vnd.github+json')
  Path(workspace_cache).mkdir(parents=True,exist_ok=True)
  self.temp=Path(tempfile.mkdtemp(prefix='session-auth-',dir=workspace_cache));self.temp.chmod(0o700)
  self.sockpath=self.temp/'auth.sock';self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.socket.bind(str(self.sockpath));self.sockpath.chmod(0o600);self.socket.listen(8);self.socket.settimeout(1);self.stop=threading.Event()
  def serve():
   while not self.stop.is_set():
    try:conn,_=self.socket.accept()
    except socket.timeout:continue
    except OSError:break
    with conn:conn.sendall(self.token.encode())
  self.thread=threading.Thread(target=serve,daemon=True);self.thread.start()
  helper=self.temp/'askpass.py';helper.write_text('''#!/usr/bin/env python3
import os,socket,sys,re
from urllib.parse import urlsplit
prompt=sys.argv[1]
u=re.search(r"https://[^'\\s]+",prompt)
if not u or urlsplit(u.group()).hostname!='github.com':sys.exit(1)
if 'username' in prompt.lower():print('x-access-token')
else:
 s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.connect(os.environ['SESSION_AUTH_SOCKET']);data=b''
 while True:
  b=s.recv(4096)
  if not b:break
  data+=b
 s.close();sys.stdout.write(data.decode()+'\\n')
''');helper.chmod(0o700)
  self.env={k:v for k,v in os.environ.items() if not k.startswith('GIT_TRACE') and k not in {'GIT_CURL_VERBOSE','GITHUB_TOKEN','GH_TOKEN'}}
  self.env.update(GIT_ASKPASS=str(helper),SESSION_AUTH_SOCKET=str(self.sockpath),GIT_TERMINAL_PROMPT='0',GIT_LFS_SKIP_SMUDGE='1')
 def __enter__(self):return self
 def __exit__(self,*exc):self.close()
 def api(self,method,path,**kw):
  path=path.lstrip('/')
  if path.startswith('repos/'):
   repo='/'.join(path.split('/')[1:3])
   if repo not in self.approved:raise RuntimeError('Repository outside this session scope')
  elif path=='user/repos' and method.upper()=='POST':
   name=kw.get('json',{}).get('name');owner=self.api('GET','user').json()['login']
   if owner+'/'+str(name) not in self.approved:raise RuntimeError('Repository creation outside session scope')
  elif path!='user':raise RuntimeError('API endpoint outside supported session scope')
  r=self.http.request(method,'https://api.github.com/'+path,timeout=60,**kw)
  if r.status_code not in {200,201,204,404,409}:raise RuntimeError('GitHub API status '+str(r.status_code)+' (response body not logged)')
  return r
 def git(self,args,cwd=None):
  from urllib.parse import urlsplit
  def check_url(url):
   u=urlsplit(url);repo=u.path.strip('/')
   if repo.endswith('.git'):repo=repo[:-4]
   if u.scheme!='https' or u.hostname!='github.com' or u.username or u.password or repo not in self.approved:raise RuntimeError('Git remote outside this session scope')
  for arg in args:
   if isinstance(arg,str) and '://' in arg:check_url(arg)
  if args and args[0] in {'push','fetch','pull'}:
   remote=next((x for x in args[1:] if not x.startswith('-')),'origin')
   if '://' not in remote:
    try:url=subprocess.check_output(['git','remote','get-url',remote],cwd=cwd,stderr=subprocess.DEVNULL).decode().strip()
    except subprocess.CalledProcessError:raise RuntimeError('Unknown Git remote') from None
    check_url(url)
  p=subprocess.run(['git','-c','credential.helper=']+list(args),cwd=cwd,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=1200)
  if p.returncode:raise RuntimeError('Git failed (code '+str(p.returncode)+'); inspect redacted repository state, not credentials')
  return p.stdout.decode().strip()
 def require_repo(self,repo,private=None,create=False):
  r=self.api('GET','repos/'+repo)
  if r.status_code==404:
   if not create:raise RuntimeError('Repository missing/inaccessible')
   self.api('POST','user/repos',json={'name':repo.split('/')[1],'private':bool(private),'auto_init':False})
   r=self.api('GET','repos/'+repo)
  info=r.json()
  if private is not None and info['private']!=private:raise RuntimeError('Repository visibility mismatch; never change automatically')
  if not info.get('permissions',{}).get('push'):raise RuntimeError('No push permission')
  return info
 def close(self):
  self.http.headers.pop('Authorization',None);self.http.close();self.stop.set();self.socket.close();self.thread.join(timeout=2);shutil.rmtree(self.temp);self.token=None;self.secret_values=()
