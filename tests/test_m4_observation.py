"""Run the shipping M4 AI/MD diagnostic actions; native event scheduling is not simulated."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET
import checks as adapter
from checks import Engine, Obj, ScriptAborted, SRC, Parser


class M4ObservationChecks(unittest.TestCase):
    setup_market = adapter.XMLDispatchChecks.setup_market

    def market(self, amounts=(80,), capacities=(100,), delayed=False):
        e, home, fleet, hub = self.setup_market(list(amounts), list(capacities), delayed=delayed)
        ship, cfg = fleet[0]
        e.roots['this'] = Obj(ship=ship, name='pilot')
        e.global_['mwexDebug'] = True
        # Supply the documented native trade contract missing from the older scheduling adapter.
        original_order = e.order
        def order(owner, offer, amount):
            result = original_order(owner, offer, amount)
            if result:
                result['state'] = 'queued'
                result['trade'] = Obj(exists=True, amount=amount, transferredamount=0, destroyedamount=0,
                    ware=offer['ware'], unitprice=offer.actual_price(owner), tradeoffer=offer,
                    buyer=offer['owner'] if offer['isbuyoffer'] else owner,
                    seller=owner if offer['isbuyoffer'] else offer['owner'])
            return result
        e.order = order
        return e, home, ship, cfg, hub

    def md(self, e):
        root = ET.parse(SRC/'md/mwex_m4_debug.xml').getroot()
        cues = {c.get('name'): c for c in root.findall('./cues/cue')}
        names = Obj({name: 'm4md.'+name for name in cues})
        e.roots['md'] = Obj(MWEX_M4_Debug=names)
        original = e.runner.action
        e.cancelled_cues = []
        counter = [0]
        def invoke(name, param, local=None):
            previous_event = e.roots.get('event')
            previous_this = e.roots.get('this')
            counter[0] += 1
            e.roots['this'] = Obj(name=name+str(counter[0]), exists=True)
            e.roots['event'] = Obj(param=param)
            try:
                env = {} if local is None else local
                e.runner.children(list(cues[name].find('actions')), env)
                return env
            finally:
                e.roots['event'] = previous_event
                e.roots['this'] = previous_this
        def action(node, local):
            if node.tag in ('cancel_cue', 'reset_cue'):
                if node.tag == 'cancel_cue':
                    e.cancelled_cues.append(e.runner.value(node.get('cue'), local))
                return
            if node.tag == 'signal_cue_instantly':
                target = e.runner.value(node.get('cue'), local)
                if isinstance(target, str) and target.startswith('m4md.'):
                    value = e.runner.value(node.get('param', 'null'), local)
                    invoke(target.split('.', 1)[1], value)
                    return
            original(node, local)
        e.runner.action = action
        return invoke

    def wave(self, e, hub, cfg):
        return e.runner.script('mule.fleet.wave', dict(hub=hub, cfg=cfg))

    def texts(self, e, event):
        return [x['text'] for x in e.disk_logs if ' '+event+' ' in x['text']]

    def test_four_streams_share_one_ordered_sequence_and_schema(self):
        e, h, ship, cfg, hub = self.market()
        self.wave(e, hub, cfg)
        invoke = self.md(e)
        invoke('Record', Obj(ship=ship, attempt=1, voyage='x', file='economy', text='TEST source=md'))
        self.assertEqual({x['name'] for x in e.disk_logs},
            {'m4-lifecycle.txt', 'm4-search.txt', 'm4-orders.txt', 'm4-economy.txt'})
        headers = [x for x in e.disk_logs if ' SESSION ' in x['text']]
        self.assertEqual(len(headers), 4)
        seq = [int(re.search(r' seq=(\d+)', x['text'])[1]) for x in e.disk_logs if ' SESSION ' not in x['text']]
        self.assertEqual(seq, list(range(1, len(seq)+1)))
        self.assertTrue(all(x['name']=='m4-search.txt' for x in e.disk_logs if ' CANDIDATE ' in x['text']))

    def test_rejection_before_queries_is_unknown_not_zero(self):
        e, h, ship, cfg, hub = self.market()
        ship['forbidden'].add(e.sources[0]['owner'])
        self.wave(e, hub, cfg)
        line = self.texts(e, 'CANDIDATE')[0]
        self.assertIn('code=native_offer_unusable', line)
        self.assertIn('supplyNet=unknown buy=unknown', line)

    def test_spread_rejection_keeps_measured_net_quantities(self):
        e, h, ship, cfg, hub = self.market()
        e.sources[0]['unitprice'] = 200
        self.wave(e, hub, cfg)
        line = self.texts(e, 'CANDIDATE')[0]
        self.assertIn('code=spread', line)
        self.assertNotIn('needNet=unknown', line)
        self.assertNotIn('needNet=0 ', line)

    def test_prefilter_sample_limit_keeps_complete_counts(self):
        e, h, ship, cfg, hub = self.market(amounts=(80,)*12)
        for ware in e.wares: ship['cargo'][ware]['max'] = 0
        result = e.runner.script('mule.fleet.plan', dict(hub=hub, cfg=cfg, relaxed=False))
        self.assertEqual(result['diagnostic']['prefilterCounts']['cargo_incompatible'], 12)
        self.assertEqual(len(self.texts(e, 'PREFILTER')), 2)
        self.assertIn('count=12', self.texts(e, 'PREFILTER_COUNTS')[0])

    def test_full_trace_preserves_queries_and_final_orders(self):
        results = []
        sizes = []
        for full in (False, True):
            e, h, ship, cfg, hub = self.market(amounts=(40,)*12, capacities=(1000,))
            e.global_['mwexM4TraceCandidates'] = full
            self.wave(e, hub, cfg)
            results.append((dict(e.queries), [(o['amount'],o['offer']['isbuyoffer']) for o in ship['orders']]))
            sizes.append(len(self.texts(e, 'CANDIDATE')))
        self.assertEqual(*results)
        self.assertGreater(sizes[1], sizes[0])

    def test_wait_log_preserves_independent_wave_and_plan_values(self):
        e, h, ship, cfg, hub = self.market()
        e.runner.script('mule.fleet.plan', dict(hub=hub,cfg=cfg,relaxed=False,debugWait=225,debugWaitingSince=0))
        line=self.texts(e,'WAIT_INPUT')[0]
        self.assertIn('waveWait=225',line)
        self.assertIn('planWait=0',line)

    def test_planner_abort_emits_one_terminal_result_through_wave(self):
        e, h, ship, cfg, hub = self.market(amounts=(40,)*20, capacities=(1000,))
        def abort(engine): raise ScriptAborted()
        e.on_wait = abort
        with self.assertRaises(ScriptAborted): self.wave(e, hub, cfg)
        terminal=self.texts(e,'RESULT')
        self.assertEqual(len(terminal),1)
        self.assertIn('stage=plan',terminal[0]);self.assertIn('cause=unknown',terminal[0])
        self.assertTrue(ship['pilot']['mwexM4Search']['logClosed'])

    def test_overlap_is_evidence_without_inventing_abort_cause(self):
        e, h, ship, cfg, hub = self.market()
        ship['pilot']['mwexM4Search']=Obj(attempt=77,phase='candidates',actor=Obj(exists=True),logClosed=False)
        e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=False))
        line=self.texts(e,'SEARCH_OVERLAP')[0]
        self.assertIn('previousAttempt=77',line)
        self.assertIn('conclusion=overlap_not_proof_of_abort',line)

    def test_order_identity_is_captured_before_native_order_disappears(self):
        e, h, ship, cfg, hub = self.market()
        self.wave(e,hub,cfg)
        self.assertEqual(len(ship['pilot']['mwexM4LogOrders']),2)
        self.assertEqual(len(self.texts(e,'ORDER_CREATED')),2)
        entry=ship['pilot']['mwexM4LogOrders'][0]
        entry['order']['exists']=False;entry['trade']['exists']=False
        invoke=self.md(e)
        invoke('OrderEvent',Obj(ship=ship,pilot=ship['pilot'],order=entry['order'],kind='finished',immediate=None))
        line=self.texts(e,'NATIVE_ORDER')[0]
        self.assertIn('order='+entry['orderId'],line)
        self.assertIn('outcome=native_event cause=unknown',line)
        self.assertNotIn('success',line)

    def test_critical_cancel_request_is_not_reported_as_removed(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        entry=ship['pilot']['mwexM4LogOrders'][0]
        invoke('OrderEvent',Obj(ship=ship,pilot=ship['pilot'],order=entry['order'],kind='cancelled',immediate=False))
        self.assertFalse(entry['terminal'])
        self.assertIn('orderExists=True',self.texts(e,'NATIVE_ORDER')[0])

    def test_native_partial_trade_is_exactly_correlated_without_economic_writes(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        entry=ship['pilot']['mwexM4LogOrders'][0]
        trade=entry['trade'];trade.update(transferredamount=17,amount=63,unitprice=104)
        before=(ship['money'],e.player['money'],list(ship['orders']),dict(e.queries))
        invoke('TradeEvent',Obj(ship=ship,pilot=ship['pilot'],offer=entry['offer'],deal=trade,kind='completed',side='buy'))
        line=self.texts(e,'NATIVE_TRADE')[0]
        self.assertIn('transferred=17',line);self.assertIn('unitPrice=104',line)
        self.assertIn('valueCr=1768',line);self.assertIn('correlation=exact_trade',line)
        self.assertEqual(before,(ship['money'],e.player['money'],list(ship['orders']),dict(e.queries)))

    def test_unmatched_trade_does_not_borrow_current_voyage(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        deal=Obj(exists=True,transferredamount=5,amount=0,unitprice=2,buyer=ship,seller=h)
        invoke('TradeEvent',Obj(ship=ship,pilot=ship['pilot'],offer=None,deal=deal,kind='completed',side='buy'))
        line=self.texts(e,'NATIVE_TRADE')[0]
        self.assertIn('correlation=unmatched',line);self.assertIn('voyage=None',line)

    def test_baseline_adopts_existing_trades_with_unknown_original_plan(self):
        e,h,ship,cfg,hub=self.market();e.global_['mwexDebug']=False;self.wave(e,hub,cfg)
        e.global_['mwexDebug']=True;invoke=self.md(e)
        orders=list(ship['orders']);queries=dict(e.queries)
        invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=True))
        self.assertEqual(len(self.texts(e,'ORDER_ADOPTED')),2)
        self.assertTrue(all('planned=unknown' in line for line in self.texts(e,'ORDER_ADOPTED')))
        self.assertEqual(ship['orders'],orders);self.assertEqual(e.queries,queries)

    def test_offer_only_cancel_has_no_invented_transfer_or_price(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        invoke('TradeEvent',Obj(ship=ship,pilot=ship['pilot'],offer=e.sources[0],deal=None,kind='cancelled',side='buy'))
        line=self.texts(e,'NATIVE_TRADE')[0]
        self.assertIn('transferred=None',line);self.assertIn('unitPrice=None',line)
        self.assertIn('internal=None',line);self.assertIn('correlation=unmatched',line)

    def test_missing_order_is_unknown_and_emitted_once(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        entry=ship['pilot']['mwexM4LogOrders'][0];entry['order']['exists']=False
        for _ in range(2):invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=False))
        lines=self.texts(e,'ORDER_GONE');self.assertEqual(len(lines),1)
        self.assertIn('outcome=unknown',lines[0]);self.assertNotIn('cancelled',lines[0])

    def test_reassignment_does_not_adopt_or_log_unrelated_trades(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        known=list(ship['pilot']['mwexM4LogOrders'])
        ship['defaultorder']=Obj(exists=True,id='TravelMule')
        unrelated=e.order(ship,e.sources[0],10)
        before=len(e.disk_logs)
        invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=False))
        invoke('OrderEvent',Obj(ship=ship,pilot=ship['pilot'],order=unrelated,kind='ready',immediate=False))
        invoke('TradeEvent',Obj(ship=ship,pilot=ship['pilot'],offer=e.sources[0],deal=unrelated['trade'],kind='started',side='buy'))
        self.assertEqual(ship['pilot']['mwexM4LogOrders'],known)
        self.assertTrue(any('scopeM4=False' in x['text'] for x in e.disk_logs[before:]))
        self.assertFalse(any('correlation=unmatched' in x['text'] for x in e.disk_logs[before:]))

    def test_reassignment_keeps_exact_tracking_of_original_m4_trade(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        entry=ship['pilot']['mwexM4LogOrders'][0]
        ship['defaultorder']=Obj(exists=True,id='TravelMule')
        entry['trade']['transferredamount']=17
        invoke('TradeEvent',Obj(ship=ship,pilot=ship['pilot'],offer=entry['offer'],deal=entry['trade'],kind='completed',side='buy'))
        line=self.texts(e,'NATIVE_TRADE')[0]
        self.assertIn('transferred=17',line);self.assertIn('correlation=exact_trade',line)

    def test_md_debug_off_is_silent_and_cannot_query_or_modify_orders(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        e.global_['mwexDebug']=False
        before=(len(e.disk_logs),dict(e.queries),list(ship['orders']))
        invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=True))
        invoke('OrderEvent',Obj(ship=ship,pilot=ship['pilot'],order=ship['orders'][0],kind='finished',immediate=True))
        self.assertEqual(before,(len(e.disk_logs),dict(e.queries),list(ship['orders'])))

    def test_md_and_ai_expressions_parse_against_adapter(self):
        for file in (SRC/'md/mwex_m4_debug.xml', SRC/'aiscripts/mule.fleet.observe.xml'):
            for node in ET.parse(file).getroot().iter():
                for key in ('exact','value','in','object','param','text','cue','directory'):
                    if key in node.attrib:Parser(node.get(key)).parse()

    def test_duplicate_ship_registration_keeps_one_observer(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        invoke('Register',ship)
        first=e.global_['mwexM4Log']['watches'][ship]['cue']
        invoke('Register',ship)
        self.assertIs(e.global_['mwexM4Log']['watches'][ship]['cue'],first)
        self.assertEqual(len(self.texts(e,'SHIP_BASELINE')),1)

    def test_new_observation_session_keeps_existing_orders(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        invoke('Register',ship)
        old_watch=e.global_['mwexM4Log']['watches'][ship]['cue']
        e.global_['mwexFleetHubs']=Obj({h:hub})
        old=e.global_['mwexM4Log'];orders=list(ship['orders']);queries=dict(e.queries)
        invoke('Start','game_loaded')
        self.assertIsNot(e.global_['mwexM4Log'],old)
        self.assertIn(old_watch,e.cancelled_cues)
        self.assertEqual(ship['orders'],orders);self.assertEqual(e.queries,queries)
        self.assertIn('priorHistory=not_reconstructed',self.texts(e,'BOUNDARY')[0])

    def test_source_filter_failure_is_visible_without_candidate_queries(self):
        e,h,ship,cfg,hub=self.market()
        cfg['allowAiSuppliers']=False
        self.wave(e,hub,cfg)
        self.assertFalse(self.texts(e,'CANDIDATE'))
        self.assertIn('code=source_ai_disabled',self.texts(e,'SOURCE_FILTER')[0])
        self.assertTrue(any('code=source_ai_disabled count=1' in x for x in self.texts(e,'PREFILTER_COUNTS')))


if __name__=='__main__':unittest.main(verbosity=2)
