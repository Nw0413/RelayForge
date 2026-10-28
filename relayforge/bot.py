"""RelayForge Discord/Pterodactyl runtime."""
import asyncio
from collections import deque
import json
import logging
import os
import re
import time
import aiohttp
import discord
from .core import Adapter, ERROR, PLAYER_HEADER, ANSI, command_config, env_bool, hhmm, sanitize, safe_text

log = logging.getLogger('relayforge')

class Bridge(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.game = os.getenv('GAME_TYPE', 'factorio').lower()
        self.adapter = Adapter(self.game, os.getenv('GAME_CHAT_REGEX',''), os.getenv('GAME_JOIN_REGEX',''), os.getenv('GAME_LEAVE_REGEX',''), os.getenv('GAME_SEND_COMMAND',''), env_bool('CHAT_AUTO_DETECT'))
        self.panel = os.environ['PTERODACTYL_URL'].rstrip('/')
        self.server = os.environ['PTERODACTYL_SERVER_ID']
        self.api_key = os.environ['PTERODACTYL_API_KEY']
        self.channel = int(os.environ['DISCORD_CHANNEL_ID'])
        self.admin_role = os.getenv('DISCORD_COMMAND_ROLE_ID','').strip()
        self.alert_channel = int(os.getenv('ALERT_DISCORD_CHANNEL_ID','').strip() or self.channel)
        self.alert_role = os.getenv('ALERT_DISCORD_ROLE_ID','').strip()
        self.alerts = env_bool('ALERTS_ENABLED')
        self.cpu_limit = float(os.getenv('ALERT_CPU_THRESHOLD','0') or '0')
        self.cpu_samples = max(1,int(os.getenv('ALERT_CPU_SAMPLES','4')))
        self.poll = max(5,int(os.getenv('ALERT_POLL_SECONDS','30')))
        self.error_limit = max(1,int(os.getenv('ALERT_ERROR_THRESHOLD','5')))
        self.error_window = max(1,int(os.getenv('ALERT_ERROR_WINDOW_SECONDS','120')))
        self.cooldown = max(1,int(os.getenv('ALERT_COOLDOWN_SECONDS','900')))
        self.error_times = deque()
        self.last_alert = {}
        self.high_cpu = 0
        self.paused = False
        self.http = None
        self.console_ws = None
        self.send_join_leave = env_bool('SEND_JOIN_LEAVE',True)
        self.send_gps = env_bool('SEND_GPS_PING')
        self.achievements = env_bool('ACHIEVEMENT_MODE',True)
        self.pending_players = None
        self.mention_cache = None
        self.mention_cache_time = 0.0
        self.tasks = []

    async def setup_hook(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),headers={'Authorization':f'Bearer {self.api_key}','Accept':'application/json','Content-Type':'application/json'})
        self.tasks = [asyncio.create_task(self.console_loop())]
        if self.alerts and self.cpu_limit > 0:
            self.tasks.append(asyncio.create_task(self.monitor_loop()))

    async def close(self):
        for task in self.tasks: task.cancel()
        if self.console_ws: await self.console_ws.close()
        if self.http: await self.http.close()
        await super().close()

    async def on_ready(self):
        log.info('Discord connected as %s; game=%s',self.user,self.game)

    async def api(self,method,suffix,payload=None):
        url=f'{self.panel}/api/client/servers/{self.server}{suffix}'
        async with self.http.request(method,url,json=payload) as resp:
            body=await resp.text()
            if resp.status>=400: raise RuntimeError(f'Pterodactyl {resp.status}: {body[:300]}')
            return json.loads(body) if body.strip() else None

    async def command(self,command):
        await self.api('POST','/command',{'command':command})

    async def console_loop(self):
        while not self.is_closed():
            try:
                response=await self.api('GET','/websocket')
                auth=response['data']
                async with aiohttp.ClientSession() as ws_session:
                    async with ws_session.ws_connect(auth['socket'],heartbeat=30,origin=self.panel) as ws:
                        self.console_ws=ws
                        await ws.send_json({'event':'auth','args':[auth['token']]})
                        async for incoming in ws:
                            if incoming.type!=aiohttp.WSMsgType.TEXT:
                                if incoming.type in (aiohttp.WSMsgType.ERROR,aiohttp.WSMsgType.CLOSED): break
                                continue
                            ev=json.loads(incoming.data)
                            kind=ev.get('event')
                            if kind=='auth success': log.info('Authenticated to Pterodactyl live console')
                            elif kind=='console output':
                                for chunk in ev.get('args',[]):
                                    for line in chunk.splitlines():
                                        await self.console_line(ANSI.sub('',line.strip()))
                            elif kind in ('token expiring','token expired'):
                                fresh=await self.api('GET','/websocket')
                                await ws.send_json({'event':'auth','args':[fresh['data']['token']]})
                            elif kind=='auth failure':
                                raise RuntimeError('Pterodactyl rejected console authentication')
            except asyncio.CancelledError: raise
            except Exception as exc: log.warning('Console connection failed: %s',exc)
            finally: self.console_ws=None
            await asyncio.sleep(5)

    async def send_channel(self,channel_id,text,users=(),roles=()):
        try:
            channel=self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            mentions=discord.AllowedMentions(everyone=False,users=[discord.Object(id=int(x)) for x in users],roles=[discord.Object(id=int(x)) for x in roles],replied_user=False)
            await channel.send(text[:2000],allowed_mentions=mentions)
        except Exception as exc:
            log.error('Discord send failed for channel %s: %s',channel_id,exc)

    async def alert(self,category,msg):
        if not self.alerts or not self.alert_role: return
        now=time.monotonic()
        if now-self.last_alert.get(category,-1e12)<self.cooldown: return
        self.last_alert[category]=now
        await self.send_channel(self.alert_channel,f'<@&{self.alert_role}> {msg}',roles=[self.alert_role])

    async def monitor_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(self.poll)
            try:
                res=(await self.api('GET','/resources'))['attributes']
                cpu=float(res['resources'].get('cpu_absolute') or 0)
                self.high_cpu=self.high_cpu+1 if res.get('current_state')=='running' and cpu>=self.cpu_limit else 0
                if self.high_cpu>=self.cpu_samples:
                    await self.alert('cpu',f'⚠️ **Possible game server lag:** CPU at {cpu:.1f}% for {self.high_cpu} checks. CPU is not game UPS.')
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.high_cpu=0
                log.warning('Resource check failed: %s',exc)

    async def game_mentions(self,message):
        channel=self.get_channel(self.channel) or await self.fetch_channel(self.channel)
        guild=getattr(channel,'guild',None)
        if not guild: return safe_text(message),[],[]
        stale=self.mention_cache is None or time.monotonic()-self.mention_cache_time>=600
        names={} if stale else self.mention_cache
        def add(name,typ,ident):
            key=name.casefold().strip()
            if not key: return
            target=(typ,str(ident))
            if key not in names: names[key]=target
            elif names[key]!=target: names[key]=None
        if stale:
            try:
                async for member in guild.fetch_members(limit=None):
                    add(member.name,'user',member.id)
                    add(member.display_name,'user',member.id)
                    if member.global_name: add(member.global_name,'user',member.id)
            except (discord.Forbidden,discord.HTTPException) as exc:
                log.warning('Member lookup unavailable: %s',exc)
            for role in guild.roles:
                if not role.is_default(): add(role.name,'role',role.id)
            self.mention_cache=names
            self.mention_cache_time=time.monotonic()
        users,roles=[],[]
        pattern=re.compile(r'@\{([^}\n]{1,80})\}|@([\w.-]{1,80})')
        def replace(m):
            if len(users)+len(roles)>=5: return m[0]
            key=(m[1] or m[2]).casefold()
            if key in ('everyone','here'): return m[0]
            target=names.get(key)
            if not target: return m[0]
            typ,ident=target
            group=users if typ=='user' else roles
            if ident not in group: group.append(ident)
            return f'<@{ident}>' if typ=='user' else f'<@&{ident}>'
        text=pattern.sub(replace,sanitize(message)).replace('`',"'")
        return text,users,roles

    async def console_line(self,line):
        if self.pending_players: self.track_players(line)
        if self.alerts and '[CHAT]' not in line and '[Discord]' not in line and ERROR.search(line):
            now=time.monotonic()
            while self.error_times and now-self.error_times[0]>self.error_window: self.error_times.popleft()
            self.error_times.append(now)
            if len(self.error_times)>=self.error_limit:
                self.error_times.clear()
                await self.alert('error',f'🚨 **Server error burst:** {self.error_limit} recent errors. Latest: `{safe_text(line)[:180]}`')
        if self.paused: return
        event=self.adapter.parse(line,self.send_join_leave,self.send_gps)
        if not event: return
        if event.kind=='chat' and self.game=='factorio':
            text,users,roles=await self.game_mentions(event.message)
            await self.send_channel(self.channel,f'💬 | `{safe_text(event.player)}`: {text}',users,roles)
        else:
            await self.send_channel(self.channel,event.text)

    def track_players(self,line):
        req=self.pending_players
        if req is None: return
        if req['count'] is None:
            match=PLAYER_HEADER.search(line)
            if not match: return
            req['count']=int(match[1])
            if match[2].strip(): req['names'].append(match[2].strip())
        elif len(req['names'])<req['count']:
            name=line.strip()
            if name and len(name)<=100 and not re.match(r'^\d{4}[/\-]\d\d',name) and not name.startswith('['):
                req['names'].append(name)
        if len(req['names'])>=req['count']:
            if not req['future'].done(): req['future'].set_result(req['names'][:req['count']])
            self.pending_players=None

    async def player_list(self):
        if self.pending_players: return 'A player-list request is already in progress.'
        future=asyncio.get_running_loop().create_future()
        self.pending_players={'count':None,'names':[],'future':future}
        try:
            await self.command('/players online')
            names=await asyncio.wait_for(future,timeout=6)
            return f'**Online Factorio players ({len(names)}):**\n'+ ('\n'.join('• '+safe_text(n) for n in names) if names else 'Nobody is currently connected.')
        except asyncio.TimeoutError:
            return 'Could not read the Factorio player list from the console within six seconds.'
        finally: self.pending_players=None

    async def on_message(self,m):
        if m.author.bot or m.channel.id!=self.channel or not m.content.strip(): return
        text=m.content.strip()
        if text.startswith('!'):
            await self.discord_command(m)
            return
        if self.paused: return
        nick=sanitize(getattr(m.author,'display_name',m.author.name))[:80]
        for line in text.splitlines():
            cmd=self.adapter.command(nick,sanitize(line)[:320],self.achievements)
            if cmd:
                try: await self.command(cmd)
                except Exception as exc: log.warning('Game message send failed: %s',exc)

    async def discord_command(self,m):
        config=command_config()
        parts=m.content.strip().split(' ',1)
        cmd=parts[0].lower()
        cmd=config['aliases'].get(cmd,cmd).lower()
        if cmd in [x.lower() for x in config['disabled']]: return
        arg=parts[1].strip() if len(parts)>1 else ''
        if cmd in config['custom']:
            await self.send_channel(m.channel.id,str(config['custom'][cmd])[:1900]); return
        if cmd=='!help':
            lines=['**RelayForge commands**','`!help` — show commands','`!ping` — check Discord connection','`!status` — bridge and monitoring settings']
            if self.game=='factorio': lines.append('`!players` — online Factorio players')
            lines.append('`!uptime` — game server container uptime')
            await self.send_channel(m.channel.id,'\n'.join(lines))
        elif cmd=='!ping':
            await self.send_channel(m.channel.id,'🏓 Pong — RelayForge connected.')
        elif cmd=='!status':
            await self.send_channel(m.channel.id,f'**Bridge:** {"paused" if self.paused else "running"}\n**Game:** {self.game}\n**Alerts:** {self.alerts}\n**CPU threshold:** {f"{self.cpu_limit:g}%" if self.cpu_limit else "disabled"}')
        elif cmd=='!uptime':
            try:
                data=(await self.api('GET','/resources'))['attributes']
                state,ms=data.get('current_state'),data.get('resources',{}).get('uptime')
                result=hhmm(ms) if state=='running' and ms is not None else ('Uptime unavailable from Pterodactyl.' if state=='running' else f'Game server: {state}.')
            except Exception as exc:
                log.warning('Uptime request failed: %s',exc)
                result='Could not retrieve uptime.'
            await self.send_channel(m.channel.id,result)
        elif cmd=='!players':
            await self.send_channel(m.channel.id,await self.player_list() if self.game=='factorio' else '!players is only available for Factorio.')
        elif cmd in ('!pause','!resume','!announce'):
            allowed=self.admin_role and any(str(role.id)==self.admin_role for role in getattr(m.author,'roles',[]))
            if not allowed:
                await self.send_channel(m.channel.id,'You need the configured Discord command role.'); return
            if cmd=='!pause':
                self.paused=True
                await self.send_channel(m.channel.id,'⏸️ Chat bridge paused; alerts remain enabled.')
            elif cmd=='!resume':
                self.paused=False
                await self.send_channel(m.channel.id,'▶️ Chat bridge resumed.')
            else:
                if not arg or self.paused:
                    await self.send_channel(m.channel.id,'Usage: `!announce <text>` (resume the bridge first).'); return
                command=self.adapter.command('Server',sanitize(arg)[:300],self.achievements)
                if not command:
                    await self.send_channel(m.channel.id,'No game send command configured.'); return
                try:
                    await self.command(command)
                    await self.send_channel(m.channel.id,'Announcement sent to game server.')
                except Exception as exc:
                    log.warning('Announcement failed: %s',exc)
                    await self.send_channel(m.channel.id,'Failed to send announcement.')
