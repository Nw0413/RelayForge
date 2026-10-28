import unittest
from relayforge.core import Adapter, hhmm
class TestCore(unittest.TestCase):
 def test_uptime(self): self.assertEqual(hhmm(27*3600000+35*60000),'27:35')
 def test_factorio_chat(self):
  e=Adapter().parse('[CHAT] Engineer: hello')
  self.assertEqual((e.player,e.message),('Engineer','hello'))
 def test_server_ignored(self): self.assertIsNone(Adapter().parse('[CHAT] <server>: test'))
 def test_research(self): self.assertIn('Research completed',Adapter().parse('[FGB_RESEARCH] FINISH automation-2 1').text)
 def test_generic(self): self.assertEqual(Adapter('generic',r'^CHAT <([^>]+)> (.*)$').parse('CHAT <Alex> hello').message,'hello')
 def test_generic_auto(self):
  a=Adapter('generic',auto_detect=True)
  for i in range(3): self.assertIsNone(a.parse(f'[CHAT] Steve: {i}'))
  self.assertEqual(a.parse('[CHAT] Alex: hello').message,'hello')
if __name__=='__main__': unittest.main()
