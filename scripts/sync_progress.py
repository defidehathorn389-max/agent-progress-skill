"""Publish authorized private progress, then independently clone and hash-verify.
Library API: publish(root, session, repository, verification_dir).
Use a current-session SessionGitHub; this module does not prompt for a password.
No force pushes. Receipts refer to a verified payload, not to their own commit.
"""
from pathlib import Path
import json,hashlib,datetime
import handoff

def heads(root):
 return {p.parent.name+'/'+p.name:handoff.checked_head(p)[0] for p in handoff.all_projects(root)}

def publish(root,session,repository,verification_dir):
 root=Path(root);dest=Path(verification_dir)
 if dest.exists():raise RuntimeError('Verification directory must be new')
 check=handoff.validate(root)
 if check.get('unloaded_projects'):raise RuntimeError('Partial progress checkout: full-tree sync refused; use a scoped update that preserves remote-only files')
 if not check['index_current'] or any(x['warnings'] for x in check['projects']):raise RuntimeError('Resolve local checkpoint/view warnings first')
 info=session.require_repo(repository,private=True);branch=info.get('default_branch') or 'main';url='https://github.com/'+repository+'.git'
 ref=session.api('GET','repos/'+repository+'/git/ref/heads/'+branch)
 if ref.status_code==200:
  session.git(['fetch',url,branch],root);session.git(['merge-base','--is-ancestor','FETCH_HEAD','HEAD'],root)
 for p in root.rglob('*'):
  if not p.is_file() or '.git' in p.relative_to(root).parts:continue
  if {'credentials','.credentials'}.intersection(p.relative_to(root).parts) or p.name.endswith('.enc.json'):raise RuntimeError('Credential files do not belong in progress repository')
  data=p.read_bytes()
  if any(s.encode() in data for s in session.secret_values if s):raise RuntimeError('Secret detected in progress; upload refused (value not logged)')
 session.git(['add','-A'],root)
 changed=bool(session.git(['status','--porcelain'],root))
 if changed:session.git(['-c','user.name=SkillBot','-c','user.email=skillbot@users.noreply.github.com','commit','-m','progress: checkpoint current work and preserve evidence'],root)
 payload=session.git(['rev-parse','HEAD'],root)
 if not changed and ref.status_code==200 and ref.json()['object']['sha']==payload:
  prior=root/'sync/latest.json'
  if prior.exists() and json.loads(prior.read_text()).get('checkpoints')==heads(root):
   return {'status':'NO_NEW_CHANGES_REMOTE_REF_MATCHES','commit':payload,'receipt':json.loads(prior.read_text()),'new_commit_created':False}
 session.git(['push',url,'HEAD:refs/heads/'+branch],root)
 names=[n for n in session.git(['ls-files','-z'],root).split('\0') if n]
 manifest={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names}
 session.git(['clone','--depth','1',url,str(dest)])
 if session.git(['rev-parse','HEAD'],dest)!=payload:raise RuntimeError('Remote changed before verification; reconcile before marking synced')
 for n,digest in manifest.items():
  if hashlib.sha256((dest/n).read_bytes()).hexdigest()!=digest:raise RuntimeError('Fresh-clone content mismatch')
 receipt={'schema_version':1,'backend':'github','repository':repository,'verified_payload_commit':payload,'verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),'verification':'Actual independent fresh clone; HEAD and SHA256 of all tracked payload files','verified_files':len(manifest),'checkpoints':heads(root),'files':manifest,'scope':'Proves these checkpoint IDs and payload files only; newer changes need a new receipt.'}
 handoff.atomic(root/'sync/latest.json',handoff.dump(receipt))
 session.git(['add','sync/latest.json'],root);session.git(['-c','user.name=SkillBot','-c','user.email=skillbot@users.noreply.github.com','commit','-m','sync: record independently verified checkpoint payload'],root);session.git(['push',url,'HEAD:refs/heads/'+branch],root)
 final=session.git(['rev-parse','HEAD'],root)
 final_verify=dest.with_name(dest.name+'-with-receipt');session.git(['clone','--depth','1',url,str(final_verify)])
 if session.git(['rev-parse','HEAD'],final_verify)!=final:raise RuntimeError('Receipt commit remote mismatch')
 for n in filter(None,session.git(['ls-files','-z'],root).split('\0')):
  if (root/n).read_bytes()!=(final_verify/n).read_bytes():raise RuntimeError('Final receipt checkout differs')
 return {'status':'VERIFIED','commit':final,'verified_payload_commit':payload,'receipt_path':'sync/latest.json','checkpoint_count':len(receipt['checkpoints']),'verified_final_files':len(manifest)+int('sync/latest.json' not in manifest),'receipt':receipt}
