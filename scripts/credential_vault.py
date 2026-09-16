"""Password-encrypted credential storage. No plaintext export command.
Dependency: cryptography. CLI passwords come only from a real terminal via getpass.
Agents must have current-session user authorization; do not ask again within the approved session after the user supplies a passphrase.
"""
import argparse,base64,getpass,hashlib,json,os,sys,tempfile
from pathlib import Path
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

DEFAULT_VAULT=Path('/home/user/credentials/github-token.enc.json')
PARAMS={'name':'scrypt','n':131072,'r':8,'p':1,'length':32}

def b64(b):return base64.b64encode(b).decode('ascii')
def unb64(s):return base64.b64decode(s,validate=True)
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':')).encode('utf-8')
def key_for(passphrase,salt):
 return Scrypt(salt=salt,length=32,n=131072,r=8,p=1).derive(passphrase.encode('utf-8'))

def seal(token,passphrase):
 if not token or len(token)>8192:raise ValueError('Invalid credential length')
 if len(passphrase)<16:raise ValueError('Use a strong passphrase of at least16 characters')
 salt=os.urandom(16);nonce=os.urandom(12)
 header={'version':1,'purpose':'github-api-token','cipher':'AES-256-GCM','kdf':dict(PARAMS,salt=b64(salt)),'nonce':b64(nonce)}
 key=key_for(passphrase,salt)
 ciphertext=AESGCM(key).encrypt(nonce,token.encode('utf-8'),canonical(header))
 return dict(header,ciphertext=b64(ciphertext))

def unlock(envelope,passphrase):
 # Fixed bounded parameters prevent malicious envelopes requesting excessive work.
 if set(envelope)!={'version','purpose','cipher','kdf','nonce','ciphertext'}:raise ValueError('Invalid envelope schema')
 if envelope['version']!=1 or envelope['cipher']!='AES-256-GCM' or envelope['purpose']!='github-api-token':raise ValueError('Unsupported envelope')
 kdf=dict(envelope['kdf']);salt=unb64(kdf.pop('salt'))
 if kdf!=PARAMS or len(salt)!=16:raise ValueError('Unsupported KDF settings')
 nonce=unb64(envelope['nonce']);ct=unb64(envelope['ciphertext'])
 if len(nonce)!=12 or not 17<=len(ct)<=8208:raise ValueError('Invalid encrypted payload')
 header={k:v for k,v in envelope.items() if k!='ciphertext'}
 try:return AESGCM(key_for(passphrase,salt)).decrypt(nonce,ct,canonical(header)).decode('utf-8')
 except (InvalidTag,UnicodeDecodeError):raise ValueError('Unlock failed: wrong passphrase or damaged/tampered ciphertext') from None

def read_envelope(path):
 data=Path(path).read_bytes()
 if len(data)>16384:raise ValueError('Vault exceeds supported size')
 return json.loads(data)

def save_envelope(path,envelope,replace=False):
 path=Path(path)
 if path.exists() and not replace:raise FileExistsError('Vault already exists; explicit replacement required')
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd,temp=tempfile.mkstemp(prefix='.vault-',dir=path.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'wb') as f:f.write(canonical(envelope)+b'\n');f.flush();os.fsync(f.fileno())
  os.replace(temp,path)
 finally:
  if os.path.exists(temp):os.unlink(temp)
 return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['create','check']);parser.add_argument('--vault',type=Path,default=DEFAULT_VAULT);parser.add_argument('--replace',action='store_true');args=parser.parse_args()
 if not sys.stdin.isatty():parser.error('Interactive terminal required. No password arguments, environment variables or piped input are accepted by this CLI.')
 try:
  if args.action=='create':
   token=getpass.getpass('Token (hidden): ');password=getpass.getpass('New passphrase (hidden): ')
   if password!=getpass.getpass('Repeat passphrase (hidden): '):raise ValueError('Passphrases do not match')
   env=seal(token,password);assert unlock(env,password)==token
   fingerprint=save_envelope(args.vault,env,args.replace);print('Encrypted vault saved. Ciphertext SHA256:',fingerprint)
  else:
   password=getpass.getpass('Passphrase for this use (hidden): ')
   token=unlock(read_envelope(args.vault),password)
   print('Local decryption/authentication succeeded. No GitHub request was made.')
 except (ValueError,OSError,KeyError,TypeError) as e:
  print(str(e),file=sys.stderr);return 1
 finally:
  # Drop references; Python cannot promise complete secure erasure of RAM copies.
  token=None;password=None
 return 0

if __name__=='__main__':raise SystemExit(main())
