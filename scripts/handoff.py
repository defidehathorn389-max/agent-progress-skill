#!/usr/bin/env python3
"""Local-first project handoff. Standard library only. Hashes are not signatures.
Immutable snapshots + atomic authoritative HEAD + expected-HEAD CAS + local lock.
CURRENT.md and INDEX.md are derived views. No network or credential use.
"""
import argparse,datetime,hashlib,json,os,re,sys,uuid
from pathlib import Path

STATE_KEYS={'title','status','goal','constraints','completed','pending','decisions','artifacts','evidence','risks','next_actions','avoid','sync','handoff','related_projects'}
STATUSES={'IN_PROGRESS','READY_FOR_REVIEW','COMPLETED','BLOCKED','LOCAL_READY_PENDING_SYNC','PAUSED'}
SECRET_KEYS={'password','passphrase','token','secret','api_key','access_token','authorization_header','private_key'}
SECRET_RE=re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._~+/-]{20,}')
SLUG=re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$');CID=re.compile(r'^\d{8}T\d{6}Z-[a-f0-9]{12}$')
class HandoffError(Exception):pass

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
def dump(x):return (json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def digest(data):return hashlib.sha256(data).hexdigest()
def load(p):return json.loads(Path(p).read_text())
def atomic(p,data):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_name('.'+p.name+'.'+uuid.uuid4().hex+'.tmp')
 try:
  fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(tmp,p)
  # Best-effort directory flush for durable renames where supported.
  try:
   fd=os.open(p.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
  except OSError:pass
 finally:
  if tmp.exists():tmp.unlink()

def inside(root,relative):
 p=Path(relative)
 if p.is_absolute() or '..' in p.parts:raise HandoffError('Expected a safe relative path')
 out=(Path(root)/p).resolve()
 if not out.is_relative_to(Path(root).resolve()):raise HandoffError('Path escapes allowed root')
 return out

def project_dir(root,project):
 parts=project.split('/')
 if len(parts)!=2 or not all(SLUG.fullmatch(x) for x in parts):raise HandoffError('Project must be domain/project-id using lowercase ASCII slugs')
 return inside(root,'projects/'+project)

def check_secret(x,path='state'):
 if isinstance(x,dict):
  for k,v in x.items():
   if k.lower() in SECRET_KEYS:raise HandoffError('Secret-bearing field forbidden at '+path+'.'+k)
   check_secret(v,path+'.'+k)
 elif isinstance(x,list):
  for i,v in enumerate(x):check_secret(v,path+'['+str(i)+']')
 elif isinstance(x,str) and SECRET_RE.search(x):raise HandoffError('Potential plaintext credential detected at '+path+' (value redacted)')

def validate_state(s):
 if not isinstance(s,dict) or set(s)!=STATE_KEYS:raise HandoffError('State keys must exactly match the template')
 for k in ['title','goal']:
  if not isinstance(s[k],str) or not s[k].strip():raise HandoffError('Missing text: '+k)
 if s['status'] not in STATUSES:raise HandoffError('Unsupported project status')
 for k in ['constraints','completed','pending','decisions','artifacts','evidence','risks','next_actions','avoid','related_projects']:
  if not isinstance(s[k],list):raise HandoffError(k+' must be an array')
 for k in ['sync','handoff']:
  if not isinstance(s[k],dict):raise HandoffError(k+' must be an object')
 check_secret(s)
 evidence=set()
 for e in s['evidence']:
  if not isinstance(e,dict) or not all(e.get(k) for k in ['id','kind','source','scope']):raise HandoffError('Evidence requires id/kind/source/scope')
  if e['id'] in evidence:raise HandoffError('Duplicate evidence ID')
  evidence.add(e['id'])
 for e in s['completed']:
  if not isinstance(e,dict) or not e.get('item') or not e.get('evidence'):raise HandoffError('Completed item requires evidence references')
  if not set(e['evidence']).issubset(evidence):raise HandoffError('Completed item cites unknown evidence')
 ids=set()
 for a in s['artifacts']:
  if not isinstance(a,dict) or not a.get('id') or not a.get('role'):raise HandoffError('Artifact requires id and role')
  if a['id'] in ids:raise HandoffError('Duplicate artifact ID')
  ids.add(a['id'])
  if a.get('availability') not in {'LOCAL','REMOTE_ONLY','LOCAL_AND_REMOTE'}:raise HandoffError('Invalid artifact availability')
  if not re.fullmatch(r'[a-f0-9]{64}',a.get('sha256','')):raise HandoffError('Artifact requires SHA256')
  if a['availability']!='REMOTE_ONLY' and not a.get('local_path'):raise HandoffError('Local artifact needs local_path')
  if a.get('local_path'):inside(Path.cwd(),a['local_path'])
  if a['availability']!='LOCAL' and not a.get('remote'):raise HandoffError('Remote artifact needs recovery locator')
 for r in s['related_projects']:
  if not isinstance(r,str) or len(r.split('/'))!=2 or not all(SLUG.fullmatch(x) for x in r.split('/')):raise HandoffError('Invalid related project ID')
 if not s['handoff'].get('first_action') or not isinstance(s['handoff'].get('running_operations'),list):raise HandoffError('Handoff requires first_action and running_operations')
 if len(dump(s))>65536:raise HandoffError('Keep current state below64KiB; reference detailed artifacts instead')

def checked_head(p):
 head=load(p/'HEAD.json')
 if set(head)!={'schema_version','checkpoint_id','sha256','revision'} or head['schema_version']!=1:raise HandoffError('Invalid HEAD schema')
 if not CID.fullmatch(head['checkpoint_id']):raise HandoffError('Invalid checkpoint ID')
 data=(p/'checkpoints'/(head['checkpoint_id']+'.json')).read_bytes()
 if digest(data)!=head['sha256']:raise HandoffError('HEAD checkpoint hash mismatch')
 cp=json.loads(data)
 if cp['checkpoint_id']!=head['checkpoint_id'] or cp['revision']!=head['revision']:raise HandoffError('HEAD identity/revision mismatch')
 return head,cp

def chain(p,headcp):
 seen={};active=set()
 def visit(cp):
  key=cp['checkpoint_id']
  if key in active:raise HandoffError('Checkpoint cycle detected')
  if key in seen:return
  if not CID.fullmatch(key):raise HandoffError('Invalid checkpoint ID')
  if cp['schema_version']!=1:raise HandoffError('Unsupported checkpoint schema')
  if cp['project']!=p.name or cp['domain']!=p.parent.name:raise HandoffError('Cross-project checkpoint detected')
  validate_state(cp['state']);check_secret(cp['note']);active.add(key);revs=[]
  if not isinstance(cp['parents'],list) or len(cp['parents'])>2:raise HandoffError('Invalid parents')
  if len({q['checkpoint_id'] for q in cp['parents']})!=len(cp['parents']):raise HandoffError('Duplicate parent')
  for q in cp['parents']:
   if not CID.fullmatch(q['checkpoint_id']):raise HandoffError('Unsafe parent ID')
   data=(p/'checkpoints'/(q['checkpoint_id']+'.json')).read_bytes()
   if digest(data)!=q['sha256']:raise HandoffError('Parent checkpoint hash mismatch')
   parent=json.loads(data)
   if parent['checkpoint_id']!=q['checkpoint_id']:raise HandoffError('Parent identity mismatch')
   visit(parent);revs.append(parent['revision'])
  if cp['revision']!=max(revs,default=0)+1:raise HandoffError('Non-monotonic revision')
  active.remove(key);seen[key]=cp
 visit(headcp);return seen

def current_bytes(cp):
 s=cp['state'];lines=['# '+s['title'],'','> 派生视图；权威来源为HEAD.json指向的不可变检查点。','',f"- 项目：`{cp['domain']}/{cp['project']}`",f"- 检查点：`{cp['checkpoint_id']}`；revision {cp['revision']}",f"- 更新时间：{cp['updated_at']}",f"- 状态：{s['status']}",f"- 本次变化：{cp['note']}",'']
 labels={'goal':'目标','constraints':'当前约束','completed':'已完成（附证据）','pending':'未完成','decisions':'决定与纠正','artifacts':'文件与恢复位置','evidence':'证据及其边界','risks':'风险','next_actions':'下一步','avoid':'不要重复的错误','sync':'同步状态','handoff':'交接要求','related_projects':'关联项目'}
 for k,label in labels.items():
  lines+=['## '+label,'']
  v=s[k]
  if isinstance(v,str):lines+=[v]
  elif isinstance(v,list):
   for item in v:lines+=['- '+(item if isinstance(item,str) else json.dumps(item,ensure_ascii=False,sort_keys=True))]
   if not v:lines+=['无。']
  else:lines+=['```json',json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2),'```']
  lines+=['']
 return ('\n'.join(lines)+'\n').encode()

def all_projects(root):
 return sorted(p.parent for p in (Path(root)/'projects').glob('*/*/HEAD.json'))

def index_bytes(root):
 lines=['# 跨模型进度索引','','先读GLOBAL.md与LOCATION.json；只读取相关方向。CURRENT.md是导航视图，以校验后的HEAD检查点为准。','', '实际项目进度默认私有。各项目中的历史素材同步，不等于本进度库已同步。','']
 domain=None
 for p in all_projects(root):
  h,cp=checked_head(p)
  if p.parent.name!=domain:domain=p.parent.name;lines+=['## '+domain,'']
  link=p.relative_to(Path(root)).as_posix()+'/CURRENT.md'
  title=cp['state']['title'];lines+=[f"- [{title}]({link}) — `{domain}/{p.name}` — **{cp['state']['status']}** — `{h['checkpoint_id']}`"]
 return ('\n'.join(lines)+'\n').encode()

def rebuild(root):
 for p in all_projects(root):
  _,cp=checked_head(p);chain(p,cp);atomic(p/'CURRENT.md',current_bytes(cp))
 atomic(Path(root)/'INDEX.md',index_bytes(root))

def checkpoint(root,project,state,expected,note,merge_parent=None):
 validate_state(state);check_secret(note);p=project_dir(root,project);p.mkdir(parents=True,exist_ok=True)
 lock=p/'.write.lock'
 try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 except FileExistsError:raise HandoffError('Project is locked; inspect owner/time before recovery; no automatic lock stealing')
 try:
  with os.fdopen(fd,'wb') as f:f.write(dump({'pid':os.getpid(),'created_at':now(),'expected':expected}));f.flush();os.fsync(f.fileno())
  parents=[];rev=0
  if (p/'HEAD.json').exists():
   h,old=checked_head(p);chain(p,old)
   if expected!=h['checkpoint_id']:raise HandoffError('Stale expected HEAD: reread and reconcile before writing')
   parents=[{'checkpoint_id':h['checkpoint_id'],'sha256':h['sha256']}];rev=h['revision']
  elif expected!='NEW':raise HandoffError('New project requires --expected NEW')
  if merge_parent:
   if not parents or not CID.fullmatch(merge_parent) or merge_parent==parents[0]['checkpoint_id']:raise HandoffError('Invalid merge parent')
   raw=(p/'checkpoints'/(merge_parent+'.json')).read_bytes();other=json.loads(raw)
   if other['checkpoint_id']!=merge_parent:raise HandoffError('Merge parent identity mismatch')
   chain(p,other);parents.append({'checkpoint_id':merge_parent,'sha256':digest(raw)});rev=max(rev,other['revision'])
  cid=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]
  domain,ident=project.split('/')
  cp={'schema_version':1,'checkpoint_id':cid,'parents':parents,'revision':rev+1,'domain':domain,'project':ident,'updated_at':now(),'note':note,'state':state}
  data=dump(cp);dest=p/'checkpoints'/(cid+'.json');dest.parent.mkdir(exist_ok=True)
  # Immutable exclusive creation, then fsync, then atomic authoritative HEAD.
  with dest.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  h={'schema_version':1,'checkpoint_id':cid,'sha256':digest(data),'revision':rev+1};atomic(p/'HEAD.json',dump(h))
  atomic(p/'CURRENT.md',current_bytes(cp));atomic(Path(root)/'INDEX.md',index_bytes(root));return h
 finally:lock.unlink(missing_ok=True)

def validate(root,project=None,workspace=None):
 out={'pass':True,'scope':'Local checkpoint integrity and optional local artifact hashes only; not remote freshness, semantic approval or complete secret detection','projects':[]}
 paths=[project_dir(root,project)] if project else all_projects(root)
 if not paths:raise HandoffError('No project checkpoints found')
 for p in paths:
  h,cp=checked_head(p);seen=chain(p,cp);warnings=[];verified=[]
  if not (p/'CURRENT.md').exists() or (p/'CURRENT.md').read_bytes()!=current_bytes(cp):warnings.append('CURRENT.md missing/stale; run rebuild')
  orphans=[z.stem for z in (p/'checkpoints').glob('*.json') if z.stem not in seen]
  if orphans:warnings.append('Unreferenced checkpoints require review: '+','.join(orphans))
  if (p/'.write.lock').exists():warnings.append('Project lock exists; inspect active writer before editing')
  if workspace:
   for a in cp['state']['artifacts']:
    if a['availability']=='REMOTE_ONLY':continue
    f=inside(workspace,a['local_path'])
    if not f.is_file() or digest(f.read_bytes())!=a['sha256']:raise HandoffError('Missing/changed local artifact: '+a['id']+' in '+cp['domain']+'/'+cp['project'])
    verified.append(a['id'])
  out['projects'].append({'project':cp['domain']+'/'+cp['project'],'head':h,'checkpoints_checked':len(seen),'local_artifacts_verified':verified,'warnings':warnings})
 out['index_current']=(Path(root)/'INDEX.md').exists() and (Path(root)/'INDEX.md').read_bytes()==index_bytes(root)
 return out

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--root',type=Path,default=Path('/home/user/agent-progress'));sub=ap.add_subparsers(dest='command',required=True)
 sub.add_parser('list');sub.add_parser('rebuild')
 r=sub.add_parser('read');r.add_argument('--project',required=True)
 c=sub.add_parser('checkpoint');c.add_argument('--project',required=True);c.add_argument('--expected',required=True);c.add_argument('--state',type=Path,required=True);c.add_argument('--note',required=True);c.add_argument('--merge-parent')
 v=sub.add_parser('validate');v.add_argument('--project');v.add_argument('--verify-local',action='store_true');v.add_argument('--workspace',type=Path,default=Path('/home/user'))
 a=ap.parse_args()
 try:
  if a.command=='read':
   p=project_dir(a.root,a.project);_,result=checked_head(p);chain(p,result)
  elif a.command=='checkpoint':result=checkpoint(a.root,a.project,load(a.state),a.expected,a.note,a.merge_parent)
  elif a.command=='rebuild':rebuild(a.root);result={'rebuilt':True}
  elif a.command=='validate':result=validate(a.root,a.project,a.workspace if a.verify_local else None)
  else:
   result=[]
   for p in all_projects(a.root):
    h,cp=checked_head(p);result.append({'project':cp['domain']+'/'+cp['project'],'title':cp['state']['title'],'status':cp['state']['status'],'head':h['checkpoint_id'],'updated_at':cp['updated_at']})
  print(json.dumps(result,ensure_ascii=False,indent=2));return 0
 except (HandoffError,OSError,ValueError,KeyError,TypeError) as e:
  # Never echo input state or caught raw data, which may contain secrets.
  print('Handoff failed: '+(str(e) if isinstance(e,HandoffError) else type(e).__name__),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
