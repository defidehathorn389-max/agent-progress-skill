#!/usr/bin/env python3
"""Build a private text-only handoff package from explicit workspace paths.
No network access. No credentials, encrypted vaults, large media or Git internals.
The secret scanner is only a limited guard, not complete DLP.
"""
import argparse,hashlib,json,os,sys,zipfile,tempfile
from pathlib import Path
from handoff import inside,SECRET_RE,HandoffError
SKIP_DIRS={'.git','.cache','__pycache__','node_modules','.venv','credentials','.credentials'}
TEXT_EXT={'.md','.json','.py','.txt','.srt','.csv','.toml','.yaml','.yml'}

def export(workspace,includes,output):
 root=Path(workspace).resolve();output=Path(output);entries={};excluded=[]
 for rel in includes:
  p=inside(root,rel)
  if not p.exists():raise HandoffError('Explicit include does not exist: '+rel)
  candidates=[p] if p.is_file() else sorted(p.rglob('*'))
  for f in candidates:
   if not f.is_file():continue
   path=f.relative_to(root).as_posix();parts=f.relative_to(root).parts
   if SKIP_DIRS.intersection(parts) or f.name.startswith('.env') or f.name in {'.netrc','.git-credentials','.write.lock'} or f.name.endswith(('.enc.json','.key','.pem','.tmp')):excluded.append(path);continue
   if f.suffix not in TEXT_EXT and f.name not in {'LICENSE','.gitignore','.gitattributes'}:excluded.append(path);continue
   if f.stat().st_size>2*1024*1024:raise HandoffError('Text file exceeds2MiB; reference instead of packaging: '+path)
   safe=inside(root,path);data=safe.read_bytes()
   try:text=data.decode('utf-8')
   except UnicodeDecodeError:raise HandoffError('Non-UTF8 data in selected text file: '+path)
   if SECRET_RE.search(text):raise HandoffError('Potential credential in '+path+' (value redacted)')
   entries[path]=data
 if not entries:raise HandoffError('No eligible handoff files')
 manifest={'schema_version':1,'privacy':'PRIVATE_USER_PROGRESS_DO_NOT_PUBLISH','scope':'Rules, progress and explicitly selected text evidence only. No credentials or large media. Hashes are not signatures.','files':{p:hashlib.sha256(b).hexdigest() for p,b in sorted(entries.items())},'excluded_paths':sorted(set(excluded))}
 output.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.handoff-',suffix='.zip',dir=output.parent);os.close(fd)
 try:
  with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED) as z:
   for p,b in sorted(entries.items()):z.writestr(p,b)
   z.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
  with zipfile.ZipFile(tmp) as z:
   assert z.testzip() is None
   for p,digest in manifest['files'].items():assert hashlib.sha256(z.read(p)).hexdigest()==digest
  os.chmod(tmp,0o600);os.replace(tmp,output)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 return {'path':str(output),'files':len(entries),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'scope':manifest['scope']}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,default=Path('/home/user'));p.add_argument('--include',action='append',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 try:print(json.dumps(export(a.workspace,a.include,a.output),ensure_ascii=False,indent=2));return 0
 except (OSError,HandoffError,ValueError) as e:print(str(e) if isinstance(e,HandoffError) else type(e).__name__,file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
