"""Parsing and configuration for RelayForge."""
import json, os, re
from dataclasses import dataclass
from pathlib import Path
CHAT = re.compile(r'\[CHAT\]\s+(.+?):\s+(.*)$')
JOIN = re.compile(r'\[JOIN\]\s+(.+?)\s*$')
LEAVE = re.compile(r'\[LEAVE\]\s+(.+?)\s*$')
RESEARCH = re.compile(r'\[FGB_RESEARCH\]\s+(START|FINISH)\s+([A-Za-z0-9_-]{1,160})(?:\s+(\d{1,9}))?')
PLAYER_HEADER = re.compile(r'(?i)(?:online\s+players|players\s+online|players)\s*\((\d+)\)\s*:\s*(.*)$')
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
ERROR = re.compile(r'(?i)(?:^|\s)(?:\[ERROR\]|ERROR:|Error\s+(?:[A-Za-z][A-Za-z0-9_]*\s*:|while |in |loading |parsing |opening |reading |writing |when |occurred|[0-9])|panic:|fatal error:|stack traceback:)')
KNOWN_CHAT = [
 re.compile(r'^\s*(?:\d+(?:\.\d+)?\s+)?\[CHAT\]\s+(.+?):\s+(.+)$'),
 re.compile(r'^.*?\[(?:Server thread|Async Chat Thread)[^]]*/INFO\]:\s*<([^>]+)>\s+(.+)$'),
 re.compile(r'^\s*\[CHAT\]\s*\[([^]]+)\]:\s*(.+)$'),
]
def env_bool(key, default=False):
 return os.getenv(key, str(default)).strip().lower() in ('true','yes','1')
def sanitize(s):
 s=str(s).replace('\x00','').replace('\x1b','').replace('\r',' ').replace('\n',' ')
 return s.replace('@everyone','＠everyone').replace('@here','＠here').strip()
def safe_text(s):
 return sanitize(s).replace('`',"'").replace('@','＠')[:1200]
def hhmm(milliseconds):
 minutes=max(0,int(milliseconds))//60000
 return f'{minutes//60:02d}:{minutes%60:02d}'
@dataclass
class Event:
 kind: str
 text: str
 player: str=''
 message: str=''
class Adapter:
 def __init__(self,game='factorio',chat_regex='',join_regex='',leave_regex='',send_command='',auto_detect=False):
  self.game=game.lower()
  self.chat=re.compile(chat_regex) if chat_regex else None
  self.join=re.compile(join_regex) if join_regex else None
  self.leave=re.compile(leave_regex) if leave_regex else None
  self.send_template=send_command
  self.auto_detect=auto_detect
  self.detected=None
  self.samples={}
  if self.game not in ('factorio','generic'): raise ValueError('GAME_TYPE must be factorio or generic')
  if self.chat and self.chat.groups<2: raise ValueError('GAME_CHAT_REGEX must have two capture groups')
  if self.game=='generic' and not self.chat and not auto_detect: raise ValueError('Generic mode requires GAME_CHAT_REGEX or CHAT_AUTO_DETECT=true')
 def parse(self,raw,join_leave=True,gps=False):
  line=ANSI.sub('',raw.strip())
  if self.game=='factorio':
   m=RESEARCH.search(line)
   if m:
    name=m[2].replace('-',' ')+(f' (level {m[3]})' if m[3] else '')
    return Event('research',('🔬 | Research started: ' if m[1]=='START' else '✅ | Research completed: ')+f'**{name}**')
   m=CHAT.search(line)
   if m:
    name,msg=m.groups()
    if '<server>' in name or '[Discord]' in msg or ('[gps=' in msg and not gps): return None
    return Event('chat',f'💬 | `{safe_text(name)}`: {safe_text(msg)}',name,msg)
   if join_leave:
    for regex,typ,verb in ((JOIN,'join','joined'),(LEAVE,'leave','left')):
     m=regex.search(line)
     if m: return Event(typ,f'{"🟢" if typ=="join" else "🔴"} | `{safe_text(m[1])}` {verb} the game!')
   return None
  regex=self.chat or self.detected
  if regex:
   m=regex.search(line)
   if m and len(m.groups())>=2 and '[Discord]' not in m[2]:
    return Event('chat',f'💬 | `{safe_text(m[1])}`: {safe_text(m[2])}',m[1],m[2])
  if self.auto_detect and not self.chat and not self.detected:
   for idx,candidate in enumerate(KNOWN_CHAT):
    if candidate.search(line):
     self.samples[idx]=self.samples.get(idx,0)+1
     if self.samples[idx]>=3: self.detected=candidate
     break
  if join_leave:
   for regex,typ,verb in ((self.join,'join','joined'),(self.leave,'leave','left')):
    if regex:
     m=regex.search(line)
     if m: return Event(typ,f'{"🟢" if typ=="join" else "🔴"} | `{safe_text(m[1])}` {verb} the game!')
  return None
 def command(self,nick,msg,achievements=True):
  nick,msg=sanitize(nick),sanitize(msg)
  if self.game=='factorio':
   content=f'[Discord] [{nick}]: {msg}'
   return content if achievements else '/silent-command game.print('+json.dumps(content,ensure_ascii=True)+')'
  if not self.send_template: return None
  return self.send_template.replace('{user}',nick).replace('{message}',msg)
def command_config(path='commands.json'):
 result={'disabled':[],'aliases':{},'custom':{}}
 try:
  val=json.loads(Path(path).read_text(encoding='utf-8'))
  if isinstance(val,dict):
   result.update({key:val[key] for key in result if isinstance(val.get(key),type(result[key]))})
 except (OSError,ValueError): pass
 return result
