"""Run the shipping M4 AI/MD diagnostic actions; native event scheduling is not simulated."""
from pathlib import Path
from decimal import Decimal
import re
import unittest
import xml.etree.ElementTree as ET
import checks as adapter
from checks import Engine, Obj, ScriptAborted, SRC, Parser


class NativeMoney(int):
    """Targeted money/integer-division stress model, with integer cents storage.

    This reproduces beta.14's observed truncation; it is not the native evaluator.
    """
    def __truediv__(self,other):
        assert isinstance(other,NativeMoney),('Expected a money unit denominator',other)
        sign=-1 if (self<0)!=(other<0) else 1
        return sign*(abs(int(self))//abs(int(other)))
    def __neg__(self):return NativeMoney(-int(self))
    def __mul__(self,other):
        assert int(other)==other,other
        return NativeMoney(int(self)*int(other))
    __rmul__=__mul__


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
                    if getattr(e, 'before_md_dispatch', None):e.before_md_dispatch(target, value)
                    invoke(target.split('.', 1)[1], value)
                    return
            original(node, local)
        e.runner.action = action
        def callback(name, context, param, param2=None):
            """Run a real callback in its OWN namespace, with only its Register parent bound.

            This evaluates the binding and actions; it cannot emulate native event delivery.
            """
            cue = cues['Register'].find("./cues/cue[@name='%s']" % name)
            previous_parent=e.roots.get('parent');previous_event=e.roots.get('event')
            previous_this=e.roots.get('this')
            e.roots.update(parent=context,event=Obj(param=param,param2=param2),this=Obj(name=name))
            local={}
            try:
                event=cue.find('./conditions')[0]
                binding=next(event.get(key) for key in ('object','buyer','seller') if event.get(key))
                self.assertIs(e.runner.value(binding,local),context['ship'])
                e.runner.children(list(cue.find('actions')),local)
            finally:
                e.roots.update(parent=previous_parent,event=previous_event,this=previous_this)
        invoke.callback=callback
        return invoke

    def wave(self, e, hub, cfg):
        return e.runner.script('mule.fleet.wave', dict(hub=hub, cfg=cfg))

    def texts(self, e, event):
        return [x['text'] for x in e.disk_logs if ' '+event+' ' in x['text']]

    def test_commit_reject_covers_each_normal_exit_once(self):
        for kind in ('initial','offer','spread','busy','quantity','create_buy','create_sell','success'):
            with self.subTest(kind=kind):
                e,h,ship,cfg,hub=self.market()
                draft=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
                plan=draft['plan'];self.assertTrue(plan)
                if kind=='initial':ship['cargo']['count']=1
                elif kind=='offer':e.sources[0]['exists']=False
                elif kind=='spread':e.sources[0]['unitprice']=1000
                elif kind=='busy':e.global_['mwexFleetLock']=Obj(actor=Obj(exists=True),time=e.player['age'])
                elif kind=='quantity':e.sources[0]['total']=20
                elif kind=='create_buy':e.fail_at=1
                elif kind=='create_sell':e.fail_at=2
                result=e.runner.script('mule.fleet.commit',dict(cfg=cfg,plan=plan,diagnostic=draft['diagnostic'],hub=hub))
                lines=self.texts(e,'COMMIT_REJECT')
                self.assertEqual(len(lines),0 if kind=='success' else 1)
                if lines:
                    self.assertIn('code='+result['status'],lines[0])
                    self.assertIn('side=buy',self.texts(e,'COMMIT_REJECT_LEG')[0])
                    self.assertIn('side=sell',self.texts(e,'COMMIT_REJECT_LEG')[1])

    def test_budget_and_partial_load_are_independent_observations(self):
        e,h,ship,cfg,hub=self.market(amounts=(40,)*220,capacities=(1000,))
        cfg['maxTrades']=1;cfg['minCargoUsed']=75
        result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
        self.assertFalse(result['plan'])
        self.assertTrue(result['diagnostic']['candidateBudget'])
        self.assertEqual(result['diagnostic']['status'],'budget')
        self.assertFalse(result['diagnostic']['loadRejected'])
        self.assertTrue(result['diagnostic']['logPartialLoadBlocked'])
        line=self.texts(e,'SEARCH_CONSTRAINTS')[-1]
        self.assertIn('enumerationTruncated=True',line)
        self.assertIn('acceptedPlan=False',line)
        self.assertGreater(result['diagnostic']['logLoadBlockedStates'],0)

    def test_session_distinguishes_same_game_time_and_retains_all_wall_times(self):
        e,h,ship,cfg,hub=self.market();invoke=self.md(e)
        invoke('Start','debug_enabled');first=e.global_['mwexM4Log']['session']
        invoke('Start','game_loaded');second=e.global_['mwexM4Log']['session']
        self.assertNotEqual(first,second)
        wall_rows=[row['text'] for row in e.disk_logs if ' wall=' in row['text']]
        self.assertTrue(wall_rows)
        self.assertTrue(all('wall=2026-10-25T12:00:00' in line for line in wall_rows))
        headers=[row['text'] for row in e.disk_logs if ' SESSION ' in row['text']]
        probes=[row['text'] for row in e.disk_logs if ' SESSION_MONEY_PROBE ' in row['text']]
        self.assertEqual(len(headers),8) # two sessions, each writes a constant header to four streams
        self.assertTrue(all('moneyProbeValid=True' in line for line in probes))
        self.assertTrue(all('moneyProbeCr=12.23' in line for line in probes))

    def native_money_stress(self,e):
        evaluate=e.runner.ev
        def strict(node,local):
            if node[0]=='literal' and len(node)>2:
                if node[2] in ('ct','Cr'):
                    return NativeMoney(Decimal(node[3])*(100 if node[2]=='Cr' else 1))
                if node[2] in (None,'L') and '.' not in node[3]:return int(node[3])
            if node[0]=='binary' and node[1]=='/':
                a=e.runner.ev(node[2],local);b=e.runner.ev(node[3],local)
                if isinstance(a,NativeMoney):return a/b
                if isinstance(a,int) and isinstance(b,int):
                    return (-1 if (a<0)!=(b<0) else 1)*(abs(a)//abs(b))
                return a/b
            return evaluate(node,local)
        e.runner.ev=strict

    def test_money_text_retains_cents_when_native_money_division_truncates(self):
        e,h,ship,cfg,hub=self.market();self.native_money_stress(e)
        # Reproduce the actual beta.14 false positive, not Python float division.
        self.assertEqual(e.runner.value('1223ct/1Cr',{}),12)
        self.assertTrue(e.runner.value('1223ct/1Cr == 12.23Cr/1Cr',{}))
        for origin in ('aiscripts/mule.fleet.observe.xml','md/mwex_m4_debug.xml'):
            root=ET.parse(SRC/origin).getroot()
            expression=next(n.get('exact') for n in root.iter('set_value') if n.get('name')=='$mwlog_probe_cr')
            for cents in (0,1,9,10,23,99,100,101,1223,20995,12230001,214748364899,9007199254740991,-1,-1223):
                expected=f'{"-" if cents<0 else ""}{abs(cents)//100}.{abs(cents)%100:02d}'
                with self.subTest(origin=origin,cents=cents):
                    actual=e.runner.value(expression.replace('1223ct','$money'),dict(money=NativeMoney(cents)))
                    self.assertEqual(actual,expected)

    def test_incorrect_non_null_probe_text_emits_an_error_in_every_stream(self):
        for origin in ('ai','md'):
            e,h,ship,cfg,hub=self.market();original=e.runner.value
            def incorrect(expression,local):
                if expression.startswith("'%s%s.%s%s'.[") and '1223ct' in expression:return '12'
                return original(expression,local)
            e.runner.value=incorrect
            if origin=='ai':self.wave(e,hub,cfg)
            else:self.md(e)('Start','debug_enabled')
            probes=self.texts(e,'SESSION_MONEY_PROBE')
            self.assertEqual(len(probes),4)
            self.assertTrue(all('moneyProbeValid=False' in line for line in probes))
            errors=[x['text'] for x in e.disk_logs if 'reason=session_probe_mismatch' in x['text']]
            self.assertEqual(len(errors),4)
            self.assertTrue(all('expectedCr=12.23 actualCr=12' in line for line in errors))

    def test_fractional_order_and_native_trade_use_fixed_decimal_text(self):
        e,h,ship,cfg,hub=self.market()
        draft=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg));leg=draft['plan'][0]
        order=e.order(ship,leg['source'],17)
        order['trade']['unitprice']=NativeMoney(20995)
        self.native_money_stress(e)
        e.runner.action(ET.Element('include_interrupt_actions',ref='MWEX_Log_order'),
            dict(mwlog_order=order,mwlog_order_cfg=cfg,mwlog_order_diagnostic=draft['diagnostic'],
                 mwlog_order_leg=leg,mwlog_side='buy'))
        line=self.texts(e,'ORDER_CREATED')[-1]
        self.assertIn('unitPriceCr=209.95 unitPriceCt=20995',line)
        invoke=self.md(e)
        context=invoke('Register',ship)
        facts=order['trade']
        facts.update(amount=0,transferredamount=17,buyfree=False)
        # Execute the actual callback body and NativeTrade record from shipping MD.
        invoke.callback('BuyCompleted',Obj(context),facts)
        line=self.texts(e,'NATIVE_TRADE')[-1]
        self.assertIn('unitPrice=209.95 valueCr=3569.15 unitPriceCt=20995 valueCt=356915',line)

    def test_four_streams_share_one_ordered_sequence_and_schema(self):
        e, h, ship, cfg, hub = self.market()
        self.wave(e, hub, cfg)
        invoke = self.md(e)
        invoke('Record', Obj(ship=ship, attempt=1, voyage='x', file='economy', text='TEST source=md'))
        self.assertEqual({x['name'] for x in e.disk_logs},
            {'m4-lifecycle.txt', 'm4-search.txt', 'm4-orders.txt', 'm4-economy.txt'})
        headers = [x for x in e.disk_logs if ' SESSION ' in x['text']]
        self.assertEqual(len(headers), 4)
        seq = [int(re.search(r' seq=(\d+)', x['text'])[1]) for x in e.disk_logs if 'SESSION' not in x['text']]
        self.assertEqual(seq, list(range(1, len(seq)+1)))
        self.assertTrue(all(x['name']=='m4-search.txt' for x in e.disk_logs if ' CANDIDATE ' in x['text']))

    def test_native_compatible_counter_records_and_summary_match_all_details(self):
        e,h,ship,cfg,hub=self.market(amounts=(40,)*20,capacities=(1000,))
        self.wave(e,hub,cfg)
        d=ship['pilot']['mwexM4Search']
        self.assertIsInstance(d['rejectCounts'],list)
        self.assertEqual(sum(x['count'] for x in d['rejectCounts']),len(self.texts(e,'CANDIDATE')))
        self.assertIn('countsValid=True',self.texts(e,'COUNTS_SUMMARY')[0])
        self.assertIn('detailBodiesComplete=True',self.texts(e,'COUNTS_SUMMARY')[0])
        self.assertIn('sessionComplete=True',self.texts(e,'COUNTS_SUMMARY')[0])
        self.assertEqual(self.texts(e,'LOGGER_ERROR'),[])

    def test_session_restart_mid_search_does_not_claim_complete_detail(self):
        e,h,ship,cfg,hub=self.market(amounts=(40,)*20,capacities=(1000,))
        invoke=self.md(e)
        def restart(engine):
            engine.on_wait=None
            invoke('Start','game_loaded')
        e.on_wait=restart
        e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
        summary=self.texts(e,'COUNTS_SUMMARY')[-1]
        self.assertIn('complete=True',summary)
        self.assertIn('countsValid=True',summary)
        self.assertIn('sessionComplete=False',summary)
        self.assertIn('detailBodiesComplete=False',summary)
        self.assertIn('logTextFailures=0',summary)
        self.assertEqual(self.texts(e,'LOGGER_ERROR'),[])
        # A fresh search in the new session can still claim complete detail.
        e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
        summary=self.texts(e,'COUNTS_SUMMARY')[-1]
        self.assertIn('sessionComplete=True',summary)
        self.assertIn('detailBodiesComplete=True',summary)

    def test_counter_corruption_is_reported_instead_of_silently_marked_valid(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg)
        d=ship['pilot']['mwexM4Search'];d['rejectCounts'][0]['count']+=1;d['countsLogged']=False
        e.runner.action(ET.Element('include_interrupt_actions',ref='MWEX_Fleet_counts'),dict(mwtrace_diagnostic=d))
        self.assertIn('reason=counter_mismatch',self.texts(e,'LOGGER_ERROR')[-1])
        self.assertIn('countsValid=False',self.texts(e,'COUNTS_SUMMARY')[-1])

    def test_cumulative_trade_cache_survives_repeated_events_and_dead_handles(self):
        e,h,ship,cfg,hub=self.market();e.sources[0]['unitprice']=102.55
        self.wave(e,hub,cfg);invoke=self.md(e);context=invoke('Register',ship)
        entry=ship['pilot']['mwexM4LogOrders'][0];trade=entry['trade']
        for amount,delta in ((17,17),(17,0),(31,14)):
            trade.update(transferredamount=amount,amount=80-amount)
            invoke.callback('BuyCompleted',Obj(context),trade,None)
            line=self.texts(e,'NATIVE_TRADE')[-1]
            self.assertIn('cumulativeTransferred='+str(amount),line)
            self.assertIn('deltaTransferred='+str(delta),line)
            fields=dict(re.findall(r'([A-Za-z][A-Za-z0-9]*)=([^ ]+)',line))
            self.assertAlmostEqual(float(fields['unitPrice']),102.55)
            self.assertAlmostEqual(float(fields['unitPriceCt']),10255)
            self.assertAlmostEqual(float(fields['valueCt']),10255*amount)
        trade['exists']=False
        invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=False))
        self.assertEqual(entry['lastTransferred'],31)
        self.assertAlmostEqual(entry['unitPriceCt'],10255)
        entry['order']['exists']=False
        invoke.callback('Finished',Obj(context),entry['order'])
        line=self.texts(e,'NATIVE_ORDER')[-1]
        self.assertIn('cachedTransferred=31',line)
        self.assertIn('positive_transfer_observed',self.texts(e,'ORDER_OUTCOME')[-1])
        self.assertEqual(entry['transferred'],31)

    def test_clock_and_each_row_record_native_speed(self):
        e,h,ship,cfg,hub=self.market();invoke=self.md(e)
        context=invoke('Start','debug_enabled')
        clock=ET.parse(SRC/'md/mwex_m4_debug.xml').getroot().find("./cues/cue[@name='Start']/cues/cue[@name='Clock']/actions")
        e.roots['parent']=Obj(context)
        for active,factor in ((False,1),(True,6)):
            e.player['timewarp'].update(active=active,factor=factor)
            e.runner.children(list(clock),{})
            line=self.texts(e,'CLOCK')[-1]
            self.assertIn('seta='+str(active),line);self.assertIn('factor='+str(factor),line)
            self.assertIn('wall=2026-10-25T12:00:00',line)

    def test_yield_resume_and_phase_summary_keep_scheduling_delay(self):
        e,h,ship,cfg,hub=self.market(amounts=(40,)*20,capacities=(1000,))
        def delayed(engine):engine.player['age']+=20
        e.on_wait=delayed
        self.wave(e,hub,cfg)
        self.assertTrue(self.texts(e,'YIELD'))
        phases=[x for x in self.texts(e,'PHASE_TIME') if 'phase=candidates ' in x]
        self.assertEqual(len(phases),1)
        values=dict(re.findall(r'([A-Za-z][A-Za-z0-9]*)=([^ ]+)',phases[0]))
        self.assertGreaterEqual(float(values['elapsed']),20)
        d=ship['pilot']['mwexM4Search']
        self.assertFalse(d['yieldPending']);self.assertGreaterEqual(d['yieldMaxGap'],20)

    def test_rejection_before_queries_is_unknown_not_zero(self):
        e, h, ship, cfg, hub = self.market()
        ship['forbidden'].add(e.sources[0]['owner'])
        self.wave(e, hub, cfg)
        line = self.texts(e, 'CANDIDATE')[0]
        self.assertIn('code=native_offer_unusable', line)
        self.assertIn('supplyNet=network_unavailable buyCr=price_unavailable', line)

    def test_spread_rejection_keeps_measured_net_quantities(self):
        e, h, ship, cfg, hub = self.market()
        e.sources[0]['unitprice'] = 200
        self.wave(e, hub, cfg)
        line = self.texts(e, 'CANDIDATE')[0]
        self.assertIn('code=spread', line)
        self.assertNotIn('needNet=unknown', line)
        self.assertNotIn('needNet=0 ', line)

    def test_debug_records_each_prefilter_and_complete_counts(self):
        e, h, ship, cfg, hub = self.market(amounts=(80,)*12)
        for ware in e.wares: ship['cargo'][ware]['max'] = 0
        result = e.runner.script('mule.fleet.plan', dict(hub=hub, cfg=cfg, relaxed=False))
        self.assertEqual({x['code']:x['count'] for x in result['diagnostic']['prefilterCounts']}['cargo_incompatible'], 12)
        self.assertEqual(len(self.texts(e, 'PREFILTER')), 12)
        self.assertEqual(result['diagnostic']['prefilterEmitted'],12)
        self.assertIn('count=12', self.texts(e, 'PREFILTER_COUNTS')[0])

    def test_retired_trace_preference_cannot_reduce_debug_detail_or_change_orders(self):
        results = []
        sizes = []
        for full in (False, True):
            e, h, ship, cfg, hub = self.market(amounts=(40,)*12, capacities=(1000,))
            e.global_['mwexM4TraceCandidates'] = full
            self.wave(e, hub, cfg)
            results.append((dict(e.queries), [(o['amount'],o['offer']['isbuyoffer']) for o in ship['orders']]))
            sizes.append(len(self.texts(e, 'CANDIDATE')))
        self.assertEqual(*results)
        self.assertEqual(*sizes)
        self.assertGreater(sizes[0],2)

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
        self.assertIn('stage=plan',terminal[0]);self.assertIn('cause=engine_reason_not_provided',terminal[0])
        self.assertTrue(ship['pilot']['mwexM4Search']['logClosed'])
        summary=self.texts(e,'COUNTS_SUMMARY')[-1]
        self.assertIn('complete=False',summary)
        self.assertIn('detailBodiesComplete=False',summary)
        self.assertIn('sessionComplete=True',summary)

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
        self.assertIn('outcome=native_event cause=native_reason_not_provided',line)
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
        self.assertTrue(all('planned=not_recorded' in line for line in self.texts(e,'ORDER_ADOPTED')))
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
        self.assertIn('outcome=not_observed',lines[0]);self.assertNotIn('cancelled',lines[0])

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

    def cache_then_expire_sources(self,e,hub):
        for ware in e.wares:
            e.runner.action(ET.Element('include_interrupt_actions',ref='MWEX_Fleet_sources'),
                dict(mwsources_hub=hub,mwsources_ware=ware))
        for source in e.sources:source["exists"]=False

    def test_source_filter_failure_is_visible_without_candidate_queries(self):
        e,h,ship,cfg,hub=self.market()
        self.cache_then_expire_sources(e,hub)
        self.wave(e,hub,cfg)
        self.assertFalse(self.texts(e,'CANDIDATE'))
        self.assertIn('code=source_unavailable',self.texts(e,'SOURCE_FILTER')[0])
        self.assertTrue(any('code=source_unavailable count=1' in x for x in self.texts(e,'PREFILTER_COUNTS')))

    def test_callback_namespace_binds_each_registered_ship_independently(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        context=invoke('Register',ship)
        entry=ship['pilot']['mwexM4LogOrders'][0]
        invoke.callback('Ready',Obj(context),entry['order'])
        self.assertIn('order='+entry['orderId'],self.texts(e,'NATIVE_ORDER')[-1])
        self.assertEqual(ship['pilot']['mwexM4NativeOrders'],1)
        # There is no ship or pilot variable in the callback's own namespace.
        # An unqualified $ship in either the condition or action would fail here.
        self.assertEqual(ship['orders'][0],entry['order'])

    def test_callback_captures_terminal_trade_before_handle_disappears(self):
        for parameter in (1,2):
            with self.subTest(parameter=parameter):
                e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
                context=invoke('Register',ship);entry=ship['pilot']['mwexM4LogOrders'][0]
                trade=entry['trade'];trade.update(transferredamount=17,amount=63,unitprice=104)
                def disappear(target,message):
                    if target=='m4md.TradeEvent':trade.update(exists=False,transferredamount=999,amount=999)
                e.before_md_dispatch=disappear
                invoke.callback('BuyCompleted',Obj(context),trade if parameter==1 else entry['offer'],trade if parameter==2 else None)
                line=self.texts(e,'NATIVE_TRADE')[-1]
                # Object identities may contain "999"; validate the captured numeric fields.
                fields=dict(re.findall(r'\b(\w+)=(\S+)',line))
                self.assertEqual(Decimal(fields['transferred']),17)
                self.assertEqual(Decimal(fields['remaining']),63)
                self.assertEqual(Decimal(fields['valueCr']),1768)
                self.assertEqual(fields['correlation'],'exact_trade')
                self.assertFalse(entry['terminal'])

    def test_callback_captures_order_quantity_before_handle_disappears(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        context=invoke('Register',ship);entry=ship['pilot']['mwexM4LogOrders'][0]
        entry['trade'].update(transferredamount=37,amount=43)
        def disappear(target,message):
            if target=='m4md.OrderEvent':
                entry['order']['exists']=False;entry['trade']['exists']=False
        e.before_md_dispatch=disappear
        invoke.callback('Finished',Obj(context),entry['order'])
        line=self.texts(e,'NATIVE_ORDER')[-1]
        self.assertIn('transferred=37',line);self.assertIn('remaining=43',line)
        self.assertTrue(entry['terminal']);self.assertNotIn('success',line)

    def test_counts_use_key_lists_and_close_partial_round_exactly_once(self):
        e,h,ship,cfg,hub=self.market(amounts=(40,)*20,capacities=(1000,))
        original=e.runner.action
        def strict_list(node,local):
            if node.tag=='do_for_each' and ('rejectCounts' in node.get('in','') or 'prefilterCounts' in node.get('in','')):
                self.assertIsInstance(e.runner.value(node.get('in'),local),list)
            return original(node,local)
        e.runner.action=strict_list
        e.on_wait=lambda engine:(_ for _ in ()).throw(ScriptAborted())
        with self.assertRaises(ScriptAborted):self.wave(e,hub,cfg)
        summary=self.texts(e,'COUNTS_SUMMARY')
        self.assertEqual(len(summary),1);self.assertIn('complete=False',summary[0])
        self.assertEqual(len(self.texts(e,'RESULT')),1)
        self.assertTrue(ship['pilot']['mwexM4Search']['countsLogged'])

    def test_debug_emits_every_grade_and_refresh_with_complete_counters(self):
        results=[]
        for full in (False,True):
            e,h,ship,cfg,hub=self.market(amounts=(40,)*12,capacities=(1000,))
            e.global_['mwexM4TraceCandidates']=full;self.wave(e,hub,cfg)
            diagnostic=ship['pilot']['mwexM4Search']
            results.append(({x['code']:x['count'] for x in diagnostic['rejectCounts']},{x['code']:x['count'] for x in diagnostic['prefilterCounts']},diagnostic['refreshTotal'],diagnostic['gradeTotal']))
            self.assertEqual(diagnostic['refreshEmitted'],len(self.texts(e,'CANDIDATE_REFRESH')))
            self.assertEqual(diagnostic['gradeEmitted'],len(self.texts(e,'GRADE')))
            self.assertEqual(diagnostic['refreshEmitted'],diagnostic['refreshTotal'])
            self.assertEqual(diagnostic['gradeEmitted'],diagnostic['gradeTotal'])
            self.assertTrue(diagnostic['countsComplete'])
            self.assertEqual(len(self.texts(e,'COUNTS_SUMMARY')),1)
        self.assertEqual(*results)

    def test_debug_records_every_live_refresh_regardless_of_retired_preference(self):
        block=next(x for x in ET.parse(SRC/'aiscripts/mule.fleet.plan.xml').getroot().iter('do_if')
                   if x.get('value','').startswith('@global.$mwexDebug and ($candidate.$needNet'))
        for full in (False,True):
            e,h,ship,cfg,hub=self.market();e.global_['mwexM4TraceCandidates']=full
            diagnostic=Obj(ship=ship,attempt=1,relaxed=False,refreshTotal=0,refreshEmitted=0)
            candidate=Obj(source=e.sources[0],need=e.needs[0],ware=e.wares[0],needNet=80,sourceNet=80,amount=80)
            local=dict(diagnostic=diagnostic,candidate=candidate,liveNet=Obj({e.sources[0]:40,e.needs[0]:40}))
            for _ in range(10):e.runner.children([block],local)
            self.assertEqual(diagnostic['refreshTotal'],10)
            expected=10
            self.assertEqual(diagnostic['refreshEmitted'],expected)
            self.assertEqual(len(self.texts(e,'CANDIDATE_REFRESH')),expected)

    def test_prefilter_emitted_counter_matches_each_sample_across_shared_messages(self):
        # Several filter branches share identical formatting. Each prints/increments once.
        for kind in ('dedicated','unavailable','cargo'):
            e,h,ship,cfg,hub=self.market(amounts=(40,)*6)
            if kind=='dedicated':
                cfg['dedicatedServe']=True
                remote=e.station('Other station',h['sector'])
                for need in e.needs:need['owner']=remote
            elif kind=='unavailable':self.cache_then_expire_sources(e,hub)
            else:
                for ware in e.wares:ship['cargo'][ware]['max']=0
            self.wave(e,hub,cfg)
            diagnostic=ship['pilot']['mwexM4Search']
            emitted=len(self.texts(e,'PREFILTER'))+len(self.texts(e,'SOURCE_FILTER'))
            self.assertGreater(emitted,0)
            self.assertEqual(diagnostic['prefilterEmitted'],emitted)
            self.assertEqual(sum(x['count'] for x in diagnostic['prefilterCounts']),emitted)

    def test_heartbeat_does_not_equate_persistent_object_with_running_invocation(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=False))
        line=self.texts(e,'HEARTBEAT')[-1]
        self.assertIn('searchState=closed',line);self.assertIn('invocationAlive=not_observable',line)
        self.assertNotIn('searchActorAlive',line)
        e.global_['mwexM4Log']=Obj(seq=0,session='new',watches=Obj())
        invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=False))
        self.assertIn('searchState=prior_session',self.texts(e,'HEARTBEAT')[-1])

    def test_ai_and_md_escape_line_breaks_and_report_unavailable_messages(self):
        e,h,ship,cfg,hub=self.market();invoke=self.md(e)
        invoke('Record',Obj(ship=ship,attempt=None,voyage=None,file='lifecycle',text='TEST text=a\r\nb'))
        self.assertIn('TEST text=a[CR][LF]b',e.disk_logs[-1]['text'])
        self.assertNotIn('\n',e.disk_logs[-1]['text'])
        e.runner.script('mule.fleet.trace',dict(diagnostic=Obj(ship=ship,relaxed=False),text='TEST text=a\nb'))
        self.assertIn('TEST text=a[LF]b',e.disk_logs[-1]['text'])
        invoke('Record',Obj(ship=ship,attempt=None,voyage=None,file='lifecycle',text=None))
        self.assertIn('LOGGER_ERROR reason=message_unavailable',e.disk_logs[-1]['text'])
        self.assertIn('event=MWEX_M4_Debug.Record',e.disk_logs[-1]['text'])
        self.assertIn('field=event.param.$text',e.disk_logs[-1]['text'])
        e.runner.script('mule.fleet.trace',dict(diagnostic=Obj(ship=ship,relaxed=False),text=None))
        self.assertIn('reason=trace_text_unavailable',e.disk_logs[-1]['text'])
        self.assertIn('event=MWEX_Fleet_trace',e.disk_logs[-1]['text'])
        self.assertIn('field=mwtrace_text',e.disk_logs[-1]['text'])

    def test_session_schema_reports_actual_mod_version_in_both_emitters(self):
        for origin in ('ai','md'):
            e,h,ship,cfg,hub=self.market()
            if origin=='ai':self.wave(e,hub,cfg)
            else:self.md(e)('Record',Obj(ship=None,attempt=None,voyage=None,file='lifecycle',text='TEST source=md'))
            for line in self.texts(e,'SESSION'):
                self.assertIn('schema=9',line)
                self.assertIn('scoreScale=1000000',line)
                self.assertIn('mod='+(SRC/'VERSION').read_text().strip(),line)

    def test_begin_and_clock_versions_match_session_metadata(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg)
        invoke=self.md(e);context=invoke('Start','debug_enabled')
        clock=ET.parse(SRC/'md/mwex_m4_debug.xml').getroot().find("./cues/cue[@name='Start']/cues/cue[@name='Clock']/actions")
        e.roots['parent']=Obj(context);e.runner.children(list(clock),{})
        version=(SRC/'VERSION').read_text().strip()
        headers=self.texts(e,'SESSION');self.assertEqual(len(headers),8)
        schema=re.search(r'\bschema=(\d+)',headers[0])[1]
        for event in ('SESSION','BEGIN','CLOCK'):
            lines=self.texts(e,event);self.assertTrue(lines)
            for line in lines:
                fields=dict(re.findall(r'\b(mod|schema)=([^ ]+)',line))
                self.assertEqual(fields,dict(mod=version,schema=schema))

    def test_missing_candidate_body_is_not_counted_as_emitted_detail(self):
        e,h,ship,cfg,hub=self.market();original=e.runner.value
        def unavailable_candidate(expression,local):
            if "'CANDIDATE need=" in expression:return None
            return original(expression,local)
        e.runner.value=unavailable_candidate
        self.wave(e,hub,cfg)
        d=ship['pilot']['mwexM4Search']
        self.assertGreater(d['candidateTotal'],0)
        self.assertEqual(d['candidateEmitted'],0)
        summary=self.texts(e,'COUNTS_SUMMARY')[-1]
        self.assertIn('countsValid=False',summary)
        self.assertIn('detailBodiesComplete=False',summary)
        errors=self.texts(e,'LOGGER_ERROR')
        self.assertTrue(errors)
        self.assertIn('event=CANDIDATE',errors[0])
        self.assertIn('field=mwtrace_text',errors[0])

    def fields(self,line):
        return dict(re.findall(r'\b([A-Za-z][A-Za-z0-9]*)=([^ ]+)',line))

    def test_repaired_result_keeps_search_snapshot_and_reports_committed_amount(self):
        e,h,ship,cfg,hub=self.market();cfg['minCargoUsed']=50
        ship['defaultorder'].update(cfg)
        draft=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
        snapshot=dict(draft['diagnostic']['logSearchSnapshot'])
        e.sources[0]['total']=60
        result=e.runner.script('mule.fleet.commit',dict(cfg=cfg,plan=draft['plan'],diagnostic=draft['diagnostic'],hub=hub))
        self.assertTrue(result['ok']);self.assertTrue(draft['diagnostic']['repaired'])
        f=self.fields(self.texts(e,'RESULT')[-1])
        self.assertEqual((f['searchLoadPercent'],f['committedLoadPercent']),('80.00','60.00'))
        self.assertEqual(Decimal(f['searchVolume']),80);self.assertEqual(Decimal(f['committedVolume']),60)
        self.assertEqual(f['committedState'],'orders_created')
        self.assertEqual(draft['diagnostic']['logSearchSnapshot'],snapshot)
        self.assertNotIn('observedLoadPercent',f)
        made=[self.fields(x) for x in self.texts(e,'ORDER_CREATED')]
        self.assertTrue(all(Decimal(x['queuedAmount'])==60 for x in made))
        repair=self.fields(self.texts(e,'REPAIR')[-1])
        self.assertEqual(Decimal(repair['beforeVolume']),80)
        self.assertEqual(repair['candidateLoadPercent'],'60.00')

    def test_failed_or_unsubmitted_plan_never_claims_committed_load(self):
        for reject in ('load','create'):
            e,h,ship,cfg,hub=self.market(amounts=(40 if reject=='load' else 80,))
            if reject=='create':e.fail_at=2
            self.wave(e,hub,cfg)
            f=self.fields(self.texts(e,'RESULT')[-1])
            self.assertEqual(f['committedState'],'not_committed')
            self.assertEqual(f['committedLoadState'],'not_committed')
            self.assertEqual(f['committedLoadPercent'],'None')
            self.assertEqual(f['searchLoadState'],'valid')

    def test_late_debug_enable_does_not_invent_search_snapshot(self):
        e,h,ship,cfg,hub=self.market();e.global_['mwexDebug']=False
        draft=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
        e.global_['mwexDebug']=True
        result=e.runner.script('mule.fleet.commit',dict(cfg=cfg,plan=draft['plan'],diagnostic=draft['diagnostic'],hub=hub))
        self.assertTrue(result['ok'])
        f=self.fields(self.texts(e,'RESULT')[-1])
        self.assertEqual(f['searchLoadState'],'snapshot_unavailable')
        self.assertEqual(f['committedLoadPercent'],'80.00')

    def test_load_text_uses_integer_basis_points_and_preserves_raw_ratio(self):
        e,h,ship,cfg,hub=self.market()
        from test_m4_selection import integer_division
        integer_division(e.runner)
        for volume,capacity,expected in ((59040,165000,'35.78'),(2448,165000,'1.48'),
                                         (101,100,'101.00'),(0,100,'0.00'),(100000001,300000000,'33.33')):
            local=dict(mwload_volume=volume,mwload_capacity=capacity)
            e.runner.action(ET.Element('include_interrupt_actions',ref='MWEX_Log_load'),local)
            self.assertEqual(local['mwload_out_percent'],expected)
            self.assertEqual(local['mwload_out_bps'],volume*10000//capacity)
        for volume,capacity,state in ((None,100,'volume_unavailable'),(1,None,'capacity_unavailable'),(1,0,'capacity_nonpositive')):
            local=dict(mwload_volume=volume,mwload_capacity=capacity)
            e.runner.action(ET.Element('include_interrupt_actions',ref='MWEX_Log_load'),local)
            self.assertIsNone(local['mwload_out_percent']);self.assertEqual(local['mwload_out_code'],state)

    def test_duplicate_native_terminal_callbacks_keep_one_outcome(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        context=invoke('Register',ship);entry=ship['pilot']['mwexM4LogOrders'][0]
        entry['trade'].update(transferredamount=80,amount=0)
        for _ in range(2):invoke.callback('Finished',Obj(context),entry['order'])
        self.assertEqual(len(self.texts(e,'NATIVE_ORDER')),2)
        self.assertEqual(len(self.texts(e,'ORDER_OUTCOME')),1)
        self.assertIn('full_transfer_observed',self.texts(e,'ORDER_OUTCOME')[0])

    def test_observation_baseline_flag_follows_order_history_not_poll_type(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        invoke('Snapshot',Obj(ship=ship,pilot=ship['pilot'],baseline=True))
        self.assertTrue(self.texts(e,'ORDER_OBSERVED'))
        self.assertTrue(all('baseline=False' in line for line in self.texts(e,'ORDER_OBSERVED')))

    def test_best_format_failure_reports_exact_event_fields_without_changing_dispatch(self):
        e,h,ship,cfg,hub=self.market();original=e.runner.value
        def fail(expression,local):
            if expression.startswith("'BEST "):return None
            return original(expression,local)
        e.runner.value=fail;self.wave(e,hub,cfg)
        self.assertEqual(len(ship['orders']),2)
        errors=self.texts(e,'LOGGER_ERROR');self.assertTrue(errors)
        self.assertIn('event=BEST',errors[0]);self.assertIn('fields=capacityBasis,stage,depth,volume,capacity',errors[0])

    def test_logbook_load_retains_fraction_under_integer_division(self):
        e,h,ship,cfg,hub=self.market()
        from test_m4_selection import integer_division
        integer_division(e.runner)
        draft=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
        d=draft['diagnostic'];d.update(volume=59040,capacity=165000)
        result=e.runner.script('mule.fleet.summary',dict(diagnostic=d,cfg=cfg))
        self.assertIn('35.78%',result['text'])
        self.assertIn('非实载',result['text'])
        local=dict(mwload_volume=1,mwload_capacity=0.5)
        e.runner.action(ET.Element('include_interrupt_actions',ref='MWEX_Log_load'),local)
        self.assertEqual(local['mwload_out_code'],'capacity_nonpositive')
        self.assertIsNone(local['mwload_out_percent'])

    def test_logbook_money_preserves_cents_in_all_m4_failure_templates(self):
        e,h,ship,cfg,hub=self.market()
        self.native_money_stress(e)
        cash=NativeMoney(1223);ware=Obj(name='ware',averageprice=cash)
        source=Obj(owner=Obj(knownname='source'),unitprice=cash,minamount=1)
        need=Obj(owner=Obj(knownname='receiver',money=cash),minamount=1)
        priced=Obj(price=cash,net=0)
        leg=Obj(ware=ware,buyprice=cash,sellprice=cash,need=need)
        ship['money']=cash
        local=dict(mwcandidate_ware=ware,mwcandidate_source=source,mwcandidate_buy=priced,
                   mwcandidate_need=need,mwcandidate_sell=priced,mwcandidate_ship=ship,
                   mwcandidate_amount=0,leg=leg,ship=ship,cost=cash,receiverCost=cash,
                   plan=[leg],ware=ware,removed=1,refund=cash)
        checked=[]
        for file in ('candidate','commit','recover'):
            root=ET.parse(SRC/f'aiscripts/mule.fleet.{file}.xml').getroot()
            for node in root.iter():
                expression=node.get('exact',node.get('text',''))
                if any('{61552,%s}'%key in expression for key in (4304,4306,4457,4453,4320)):
                    rendered=e.runner.value(expression,local)
                    self.assertIn('12.23',rendered)
                    self.assertNotIn('/1Cr',expression)
                    checked.append(expression)
        self.assertEqual(len(checked),5)
        # Two gaps below one Cr must still choose the smaller measured gap.
        gap=next(n for n in ET.parse(SRC/'aiscripts/mule.fleet.candidate.xml').getroot().iter('do_if')
                 if '$minGapCt?' in n.get('value',''))
        diagnostic=Obj(minGap=0,minGapCt=99,gapWare=None)
        local['mwcandidate_diagnostic']=diagnostic
        local['mwcandidate_buy']=Obj(price=NativeMoney(1223))
        local['mwcandidate_sell']=Obj(price=NativeMoney(1200))
        e.runner.children([gap],local)
        self.assertEqual(diagnostic['minGapCt'],23)
        self.assertIs(diagnostic['gapWare'],ware)

    def test_native_trade_format_failure_retains_callback_event_and_raw_money(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        context=invoke('Register',ship);entry=ship['pilot']['mwexM4LogOrders'][0]
        entry['trade'].update(transferredamount=17,amount=63)
        original=e.runner.ev
        def fail(node,local):
            if node[0]=='format' and node[1][0]=='literal' and node[1][1].startswith('NATIVE_TRADE '):return None
            return original(node,local)
        e.runner.ev=fail
        invoke.callback('BuyCompleted',Obj(context),entry['trade'])
        error=self.texts(e,'LOGGER_ERROR')[-1]
        self.assertIn('event=NATIVE_TRADE',error)
        self.assertIn('unitPrice,valueCr,unitPriceCt,valueCt',error)
        f=self.fields(error);self.assertEqual(Decimal(f['rawUnitPriceCt']),10000)
        self.assertEqual(Decimal(f['rawValueCt']),170000)

    def test_logging_action_libraries_have_no_recursive_include_cycle(self):
        libraries={x.get('name'):x for p in (SRC/'aiscripts').glob('*.xml')
                   for x in ET.parse(p).getroot().findall('./interrupts/library/actions')}
        def walk(name,active):
            self.assertNotIn(name,active,'Recursive action includes cannot be expanded by X4')
            for child in libraries[name].iter('include_interrupt_actions'):
                walk(child.get('ref'),active+(name,))
        for name in ('MWEX_Fleet_trace','MWEX_Fleet_counts'):walk(name,())

    def test_options_expose_one_debug_checkbox(self):
        e,h,ship,cfg,hub=self.market()
        root=ET.parse(SRC/'md/mwex_options.xml').getroot()
        api=Obj({name:'menu.'+name for name in ('Add_Row','Make_CheckBox','Make_Text')})
        e.roots['md']=Obj(Simple_Menu_API=api)
        e.runner.children(list(root.find("./cues/cue[@name='Open']/actions")),{})
        boxes=[x['param'] for x in e.cue_signals if x['cue']=='menu.Make_CheckBox']
        self.assertEqual(len(boxes),1);self.assertEqual(boxes[0]['id'],'mwex_debug')

    def test_reloading_debug_ignores_old_false_trace_setting_and_uses_key_lists(self):
        e,h,ship,cfg,hub=self.market();self.wave(e,hub,cfg);invoke=self.md(e)
        invoke('Register',ship);old=e.global_['mwexM4Log']['watches'][ship]['cue']
        e.global_['mwexFleetHubs']=Obj({h:hub});e.global_['mwexM4TraceCandidates']=False
        original=e.runner.action
        def strict_lists(node,local):
            if node.tag=='do_for_each':self.assertIsInstance(e.runner.value(node.get('in'),local),list)
            return original(node,local)
        e.runner.action=strict_lists
        invoke('Start','game_loaded')
        self.assertIn(old,e.cancelled_cues)
        self.assertEqual(len(e.global_['mwexM4Log']['watches']),1)
        self.assertNotIn('mwexM4TraceCandidates',e.global_)

    def test_single_debug_toggle_stops_logging_and_restores_full_trace(self):
        e,h,ship,cfg,hub=self.market();invoke=self.md(e)
        actions=ET.parse(SRC/'md/mwex_options.xml').getroot().find("./cues/cue[@name='DebugChanged']/actions")
        def toggle(value):
            e.roots['event']=Obj(param=Obj(checked=value));e.runner.children(list(actions),{})
        toggle(0)
        before=len(e.disk_logs)
        e.runner.script('mule.fleet.trace',dict(diagnostic=Obj(ship=ship,relaxed=False),text='TEST disabled=true'))
        self.assertEqual(len(e.disk_logs),before)
        e.global_['mwexM4TraceCandidates']=False
        toggle(1)
        self.assertTrue(e.global_['mwexDebug']);self.assertNotIn('mwexM4TraceCandidates',e.global_)
        self.assertIn('reason=debug_enabled',self.texts(e,'BOUNDARY')[-1])


if __name__=='__main__':unittest.main(verbosity=2)
