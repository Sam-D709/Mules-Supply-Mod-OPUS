"""Beta.4 regressions executed against the XML adapter from checks.py."""

import unittest
import re

if 'XMLDispatchChecks' not in globals():
    from checks import XMLDispatchChecks, Engine, Obj, ET, SRC, ScriptAborted

class SchedulerChecks(unittest.TestCase):
    setup_market = XMLDispatchChecks.setup_market

    def test_wait_snapshot_controls_threshold_even_if_blackboard_changes(self):
        for debug in (False,True):
            for age,threshold,has_plan in ((0,75,False),(120,50,False),(300,25,True),(360,25,True)):
                e,h,f,hub=self.setup_market([40],[100]);ship,cfg=f[0]
                cfg['minCargoUsed']=75;e.global_['mwexDebug']=debug;e.player['age']=1000
                ship['pilot']['mwexFleetAttempt']=Obj(waitingSince=999,misses=0)
                result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=False,inputWaitAge=age,inputWaitingSince=1000-age))
                self.assertEqual(result['diagnostic']['waitAge'],age)
                self.assertEqual(result['diagnostic']['minLoad'],threshold)
                self.assertEqual(bool(result['plan']),has_plan)

    def test_candidate_batches_survive_integer_division_semantics(self):
        e,h,f,hub=self.setup_market([20]*80,[100]);ship,cfg=f[0]
        for source in e.sources:source['unitprice']=200
        actual=e.runner.action;evaluate=e.runner.ev;yield_checks=[]
        def integer_checks(node,local):
            # Native integer division stress mode, narrowly scoped to the old batch condition.
            if node[0]=='binary' and node[1]=='/' and node[2]==('var','$checks'):
                return int(evaluate(node[2],local)/evaluate(node[3],local))
            return evaluate(node,local)
        def capture(node,local):
            if node.tag=='wait' and local.get('diagnostic',{}).get('phase')=='candidates':yield_checks.append(local['checks'])
            return actual(node,local)
        e.runner.ev=integer_checks;e.runner.action=capture
        self.plan(e,hub,cfg)
        self.assertEqual(yield_checks,[16,32,48,64,80])

    def wave(self,e,hub,ship,cfg):
        e.player['age']+=31
        e.roots['this']=ship['pilot'];ship['pilot']['ship']=ship
        return e.runner.script('mule.fleet.wave',dict(hub=hub,cfg=cfg))

    def plan(self,e,hub,cfg,relaxed=False):
        return e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=relaxed))

    def event(self,e,offer):
        e.roots['event']=Obj(param=offer)
        actions=ET.parse(SRC/'md/mwex_capacity.xml').getroot().find('./cues/cue[@name="Discovered"]/actions')
        e.runner.children(list(actions),{})

    def test_full_supplier_beats_nearest_tiny_anchor(self):
        e,h,f,hub=self.setup_market([1000],[1000]);s,c=f[0]
        e.sources[0]['total']=1
        remote=e.sector('One jump');e.connect(h['sector'],remote)
        full=e.offer(e.station('Full',remote,'ai'),e.wares[0],1000,100,False)
        r=self.plan(e,e.hub(h,f),c)
        self.assertTrue(r['plan']);self.assertIs(r['plan'][0]['source'],full)
        self.assertEqual(r['plan'][0]['amount'],1000)

    def test_adequate_near_supply_beats_far_fullest(self):
        e,h,f,hub=self.setup_market([1000],[1000]);s,c=f[0]
        e.sources[0]['total']=800
        last=h['sector']
        for i in range(4):
            sector=e.sector(str(i));e.connect(last,sector);last=sector
        e.offer(e.station('Far full',last,'ai'),e.wares[0],1000,99,False)
        r=self.plan(e,e.hub(h,f),c)
        self.assertEqual(len(r['plan']),1);self.assertIs(r['plan'][0]['source'],e.sources[0])
        self.assertEqual(r['plan'][0]['amount'],800)

    def test_mixed_categories_can_rescue_combined_threshold(self):
        e,h,f,hub=self.setup_market([400,400],[1000]);s,c=f[0]
        h['products'][e.wares[1]]['exists']=True
        r=self.plan(e,hub,c)
        self.assertEqual(len(r['plan']),2)
        self.assertEqual(sum(x['amount']*x['ware']['volume'] for x in r['plan']),800)

    def test_same_ware_multiple_suppliers_use_individual_lots(self):
        e,h,f,hub=self.setup_market([1000],[1000]);s,c=f[0];e.sources[0]['total']=400
        e.offer(e.station('Second',h['sector'],'ai'),e.wares[0],400,100,False)
        e.offer(e.station('Third',h['sector'],'ai'),e.wares[0],200,100,False)
        r=self.plan(e,e.hub(h,f),c)
        self.assertGreaterEqual(len(r['plan']),2);self.assertLessEqual(len(r['plan']),3)
        self.assertGreaterEqual(sum(x['amount'] for x in r['plan']),700)
        self.assertEqual(len({x['source'] for x in r['plan']}),len(r['plan']))

    def test_three_complete_load_failures_then_fourth_relaxes(self):
        e,h,f,hub=self.setup_market([10],[100]);s,c=f[0]
        for count in (1,2,3):
            self.wave(e,hub,s,c)
            self.assertEqual(s['orders'],[])
            self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],count)
        self.wave(e,hub,s,c)
        self.assertEqual(len(s['orders']),2)
        self.assertEqual(s['orders'][0]['amount'],10)
        self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],0)
        self.assertEqual(c['minCargoUsed'],70)

    def test_nonload_failure_breaks_consecutive_load_count(self):
        e,h,f,hub=self.setup_market([10],[100]);s,c=f[0]
        self.wave(e,hub,s,c);self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],1)
        e.sources[0]['unitprice']=200
        self.wave(e,hub,s,c);self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],0)
        e.sources[0]['unitprice']=100
        self.wave(e,hub,s,c);self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],1)
        c['signature']='changed'
        self.wave(e,hub,s,c);self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],1)

    def test_second_pilot_dispatches_during_first_pilots_yield(self):
        e,h,f,hub=self.setup_market([1000],[100,100],delayed=True);a,ac=f[0];b,bc=f[1]
        def interleave(engine):
            engine.on_wait=None
            self.assertNotIn('mwexFleetLock',engine.global_)
            old=engine.roots.get('this')
            self.wave(engine,hub,b,bc)
            self.assertEqual(len(b['orders']),2)
            engine.roots['this']=old
        e.on_wait=interleave
        self.wave(e,hub,a,ac)
        self.assertEqual(len(a['orders']),2)
        self.assertEqual(e.runner.script('mule.fleet.net',{'offer':e.needs[0]})['net'],800)

    def test_stale_drafts_cannot_overbook_delayed_native_quotes(self):
        e,h,f,hub=self.setup_market([80],[100,100],delayed=True)
        a,ac=f[0];b,bc=f[1]
        pa=self.plan(e,hub,ac)['plan'];pb=self.plan(e,hub,bc)['plan']
        self.assertTrue(pa);self.assertTrue(pb)
        self.assertTrue(e.runner.script('mule.fleet.commit',dict(cfg=ac,plan=pa))['ok'])
        self.assertFalse(e.runner.script('mule.fleet.commit',dict(cfg=bc,plan=pb))['ok'])
        self.assertEqual(b['orders'],[]);self.assertEqual(e.created,2)
        self.assertNotIn('mwexFleetLock',e.global_)

    def test_commit_busy_preserves_foreign_token(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];p=self.plan(e,hub,c)['plan']
        token=Obj(actor=Obj(exists=True),time=e.player['age']);e.global_['mwexFleetLock']=token
        r=e.runner.script('mule.fleet.commit',dict(cfg=c,plan=p))
        self.assertFalse(r['ok']);self.assertEqual(r['failure'],'commit_busy')
        self.assertIs(e.global_['mwexFleetLock'],token);self.assertEqual(e.created,0)

    def test_commit_has_no_yield_and_releases_token_on_every_outcome(self):
        for fail in (None,1,2):
            with self.subTest(fail=fail):
                e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];p=self.plan(e,hub,c)['plan']
                e.fail_at=fail
                def forbidden(engine):self.fail('Commit yielded with a live draft')
                e.on_wait=forbidden
                r=e.runner.script('mule.fleet.commit',dict(cfg=c,plan=p))
                self.assertEqual(r['ok'],fail is None);self.assertNotIn('mwexFleetLock',e.global_)

    def test_owned_receiver_price_change_can_still_commit(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];e.sources[0]['owner']['owner']='player'
        p=self.plan(e,hub,c)['plan'];e.needs[0]['unitprice']=1
        self.assertTrue(e.runner.script('mule.fleet.commit',dict(cfg=c,plan=p))['ok'])
        self.assertEqual(p[0]['sellprice'],1)

    def test_shared_discovery_filters_thousands_of_irrelevant_npc_buys(self):
        e,h,f,hub=self.setup_market([80],[100]);ware=e.wares[0]
        for i in range(1000):e.offer(e.station('NPC'+str(i),h['sector'],'ai'),ware,80,110,True)
        own=e.station('Other owned',h['sector']);owned=e.offer(own,ware,80,110,True)
        store=e.station('Build',h['sector']);store.update({'class':'buildstorage','base':h})
        build=e.offer(store,ware,80,110,True)
        e.runner.script('mule.fleet.discover',dict(hub=hub))
        self.assertCountEqual(hub['needs'],[e.needs[0],owned,build])
        self.assertEqual(e.queries['find_buy_offer'],1)
        self.assertEqual(e.queries['find_sell_offer'],1)

    def test_npc_home_discovery_keeps_explicit_home_only(self):
        e,h,f,hub=self.setup_market([80],[100]);h['owner']='ai'
        e.offer(e.station('Unrelated NPC',h['sector'],'ai'),e.wares[0],80,110,True)
        e.runner.script('mule.fleet.discover',dict(hub=hub))
        self.assertEqual(hub['needs'],[e.needs[0]])

    def test_discovery_event_during_snapshot_forces_followup_refresh(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];hub['range']=10
        e.global_['mwexFleetHubs']=Obj({h:hub})
        # Ensure snapshot processing yields after buy discovery has returned.
        for i in range(128):e.offer(e.station('NPC'+str(i),h['sector'],'ai'),e.wares[0],1,110,True)
        build=Obj(actor=s['pilot'],time=0);hub['building']=build
        def discovered(engine):
            engine.on_wait=None
            new=engine.station('New receiver',h['sector'])
            offer=engine.offer(new,e.wares[0],80,110,True)
            self.event(engine,offer)
        e.on_wait=discovered
        e.runner.script('mule.fleet.discover',dict(hub=hub))
        self.assertTrue(hub['cacheDirty'])
        hub['building']=Obj(actor=s['pilot'],time=e.player['age'])
        e.runner.script('mule.fleet.discover',dict(hub=hub))
        self.assertTrue(any(n['owner']['name']=='New receiver' for n in hub['needs']))
        self.assertFalse(hub['cacheDirty'])

    def test_stale_builder_cannot_publish_over_replacement(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0]
        for i in range(128):e.offer(e.station(str(i),h['sector'],'ai'),e.wares[0],1,110,True)
        original=hub['needs'];hub['building']=Obj(actor=s['pilot'],time=0)
        def replace(engine):
            engine.on_wait=None;hub['building']=Obj(actor=Obj(exists=True),time=1)
        e.on_wait=replace;e.runner.script('mule.fleet.discover',dict(hub=hub))
        self.assertIs(hub['needs'],original)

    def test_new_supplier_event_immediately_usable(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];e.sources[0]['total']=0
        hub['range']=10;e.global_['mwexFleetHubs']=Obj({h:hub})
        source=e.offer(e.station('New NPC factory',h['sector'],'ai'),e.wares[0],80,100,False)
        self.event(e,source)
        self.wave(e,hub,s,c)
        self.assertIs(s['orders'][0]['offer'],source)

    def test_rotating_suppliers_eventually_reach_legal_middle_entry(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];e.sources.clear()
        good=None
        for i in range(30):
            source=e.offer(e.station('Source'+str(i),h['sector'],'ai'),e.wares[0],100+i,100+i,False)
            if i==15:good=source
            else:s['forbidden'].add(source['owner'])
        e.needs[0]['unitprice']=200;hub=e.hub(h,f)
        for i in range(17):
            self.wave(e,hub,s,c)
            if s['orders']:break
        self.assertTrue(s['orders']);self.assertIs(s['orders'][0]['offer'],good)

    def test_crowded_need_pool_rotates_with_fixed_budgets(self):
        e,h,f,hub=self.setup_market([500-i for i in range(205)],[100]);s,c=f[0]
        for source in e.sources[:-1]:source['unitprice']=200
        hub=e.hub(h,f)
        r=self.plan(e,hub,c)
        self.assertFalse(r['plan']);self.assertTrue(r['diagnostic']['budgetExhausted'])
        self.assertLessEqual(r['diagnostic']['checks'],192)
        r=self.plan(e,hub,c)
        self.assertTrue(r['plan']);self.assertIs(r['plan'][0]['ware'],e.wares[-1])
        self.assertLessEqual(r['diagnostic']['checks'],192)
        self.assertLessEqual(r['diagnostic']['expanded'],128)

    def test_budget_exhaustion_does_not_count_as_load_failure(self):
        e,h,f,hub=self.setup_market([500-i for i in range(200)],[100]);s,c=f[0]
        for source in e.sources:source['unitprice']=200
        self.wave(e,hub,s,c)
        self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],0)

    def test_upgrade_patches_unwind_old_waves_and_load_preserves_orders(self):
        run=ET.parse(SRC/'aiscripts/mule.fleet.run.xml').getroot()
        self.assertEqual(run.find('./patch[@sinceversion="9"]/resume').get('label'),'register')
        for script in ('wave','plan','discover','commit','extend','journal','summary'):
            root=ET.parse(SRC/f'aiscripts/mule.fleet.{script}.xml').getroot()
            version=root.get('version')
            self.assertIsNotNone(root.find(f'./patch[@sinceversion="{version}"]/return'))
        e,h,f,hub=self.setup_market([80],[100],delayed=True);s,c=f[0]
        self.wave(e,hub,s,c);orders=list(s['orders']);holds=list(e.global_['mwexFleetPending'])
        hub['building']=Obj(actor=s['pilot'],time=0);e.global_['mwexFleetLock']=Obj(actor=s['pilot'],time=0)
        e.global_['mwexFleetHubs']=Obj({h:hub})
        actions=ET.parse(SRC/'md/mwex_capacity.xml').getroot().find('./cues/cue[@name="Loaded"]/actions')
        e.runner.children(list(actions),{})
        self.assertEqual(s['orders'],orders);self.assertEqual(e.global_['mwexFleetPending'],holds)
        self.assertNotIn('building',hub);self.assertNotIn('mwexFleetLock',e.global_)
        self.assertTrue(hub['cacheDirty'])

    def test_localization_ids_are_unique_per_page(self):
        for path in (SRC/'t').glob('*.xml'):
            for page in ET.parse(path).getroot().iter('page'):
                ids=[t.get('id') for t in page.findall('t')]
                self.assertEqual(len(ids),len(set(ids)),str(path))

    def test_failed_attempt_keeps_home_alert_throttling_without_fleet_sweep(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];e.sources[0]['unitprice']=200
        self.wave(e,hub,s,c)
        alerts=[x for x in e.signals if x['param']=='mwex_capacity_warning']
        self.assertEqual(len(alerts),1)
        self.assertIs(alerts[0]['param2']['ware'],e.wares[0])
        self.wave(e,hub,s,c)
        self.assertEqual(len([x for x in e.signals if x['param']=='mwex_capacity_warning']),1)

    def test_candidate_budget_does_not_cancel_secondary_route_search(self):
        e,h,f,hub=self.setup_market([60,30]+[1]*200,[100]);s,c=f[0]
        for source in e.sources[2:]:source['unitprice']=200
        result=self.plan(e,hub,c)
        self.assertEqual(len(result['plan']),2)
        self.assertEqual(sum(x['amount'] for x in result['plan']),90)
        self.assertTrue(result['diagnostic']['candidateBudget'])
        self.assertEqual(result['diagnostic']['checks'],192)
        self.assertGreater(result['diagnostic']['secondaryChecks'],0)
        self.assertLessEqual(result['diagnostic']['expanded'],128)
        self.assertFalse(result['diagnostic']['loadRejected'])

    def test_pool_quantities_refresh_after_other_ship_trades_during_yield(self):
        e,h,f,hub=self.setup_market([100,80]+[1]*20,[100]);s,c=f[0]
        for source in e.sources[2:]:source['unitprice']=200
        changed=[]
        def consumed(engine):
            engine.on_wait=None;e.sources[0]['total']=20;changed.append(True)
        e.on_wait=consumed
        result=self.plan(e,hub,c)
        self.assertTrue(changed)
        self.assertTrue(result['plan'])
        source_amount=sum(x['amount'] for x in result['plan'] if x['source'] is e.sources[0])
        self.assertLessEqual(source_amount,20)
        self.assertGreaterEqual(sum(x['amount'] for x in result['plan']),70)
        self.assertTrue(e.runner.script('mule.fleet.commit',dict(cfg=c,plan=result['plan']))['ok'])

    def test_log_counts_actual_secondary_extensions_when_none_fit_corridor(self):
        e,h,f,hub=self.setup_market([60,30],[100]);s,c=f[0]
        remote=e.sector('Remote');e.connect(h['sector'],remote)
        e.sources[1]['owner']['sector']=remote
        c['corridorDepth']=0;c['origin']['corridorDepth']=0
        result=self.plan(e,e.hub(h,f),c)
        diag=result['diagnostic']
        self.assertGreater(diag['secondaryChecks'],0);self.assertGreater(diag['range'],0)
        text=e.runner.script('mule.fleet.summary',dict(cfg=c,diagnostic=diag))['text']
        self.assertIn('副单：尝试',text)
        self.assertNotIn('其中副单搜索0',text)

    def test_single_lot_setting_explicitly_explains_skipped_secondary_search(self):
        e,h,f,hub=self.setup_market([60,30],[100]);s,c=f[0]
        c['maxTrades']=1;c['origin']['maxTrades']=1
        self.wave(e,hub,s,c)
        log=next(x for x in e.logbook if x['object'] is s)
        self.assertIn('副单未搜索：每趟上限设为1笔',log['text'])
        self.assertIn('装载不足',log['title'])
        self.assertLess(len(log['text']),400)

    def test_no_primary_is_not_reported_as_failed_secondary_search(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];e.sources[0]['unitprice']=200
        self.wave(e,hub,s,c)
        log=next(x for x in e.logbook if x['object'] is s)
        self.assertIn('没有可作为主单的候选',log['text'])
        self.assertNotIn('未找到可执行的下一笔',log['text'])
        self.assertNotIn('bool',log['text']);self.assertNotIn('优先玩家货源=',log['text'])

    def test_changed_commit_quantity_gets_one_concrete_human_report(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0]
        original=e.runner.script
        def consume_before_commit(name,params):
            if name=='mule.fleet.commit':e.sources[0]['total']=20
            return original(name,params)
        e.runner.script=consume_before_commit
        self.wave(e,hub,s,c)
        logs=[x for x in e.logbook if x['object'] is s]
        self.assertEqual(len(logs),1)
        self.assertIn('提交失败',logs[0]['title'])
        self.assertTrue(logs[0]['text'].startswith('提交失败：Source0的w0余量不足'))
        self.assertRegex(logs[0]['text'],r'计划80(?:\.0)?件');self.assertIn('现可用20件',logs[0]['text'])
        self.assertNotIn('COMMIT_CHECK',logs[0]['text'])
        self.assertNotIn('价格检查失败',logs[0]['text'])
        self.assertNotIn('待命：没有提交采购',logs[0]['text'])
        self.assertEqual(s['orders'],[])
        self.assertLess(len(logs[0]['text']),400)

    def test_failure_class_change_is_logged_without_waiting_ten_minutes(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];e.sources[0]['unitprice']=200
        self.wave(e,hub,s,c);before=len(e.logbook)
        e.sources[0]['unitprice']=100;e.sources[0]['total']=10
        self.wave(e,hub,s,c)
        self.assertEqual(len(e.logbook),before+1)
        self.assertIn('装载不足',e.logbook[-1]['title'])

    def test_debug_results_survive_player_log_cooldown_with_attempt_numbers(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0]
        e.global_['mwexDebug']=True;e.sources[0]['unitprice']=200
        self.wave(e,hub,s,c);before=len(e.logbook)
        self.wave(e,hub,s,c)
        self.assertEqual(len(e.logbook),before)
        events=[x['text'] for x in e.disk_logs if ' RESULT ' in x['text']]
        self.assertEqual(len(events),2)
        self.assertTrue(all(x.startswith('M4 t=') and ' ship=ship0 ' in x for x in events))
        self.assertIn('attempt=1',events[0]);self.assertIn('attempt=2',events[1])
        self.assertTrue(any(' CANDIDATE ' in x['text'] and 'reason=' in x['text'] for x in e.disk_logs))
        self.assertEqual(s['orders'],[])

    def test_debug_has_secondary_decisions_and_committed_legs_for_same_attempt(self):
        e,h,f,hub=self.setup_market([50,40],[100]);s,c=f[0];e.global_['mwexDebug']=True
        self.wave(e,hub,s,c)
        texts=[x['text'] for x in e.disk_logs]
        for event in (' BEGIN ',' CANDIDATE ',' EXTEND depth=2',' END ',' DRAFT_LEG ',' COMMIT ',' COMMIT_LEG ',' RESULT '):
            selected=[x for x in texts if event in x]
            self.assertTrue(selected,event)
            self.assertTrue(all('ship=ship0 ' in x and re.search(r' attempt=1(?:\.0)? ', x) for x in selected))
        self.assertEqual(len(s['orders']),4)

    def test_commit_creation_failure_is_one_throttled_report_and_releases_lock(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];e.fail_at=1
        self.wave(e,hub,s,c)
        logs=[x for x in e.logbook if x['object'] is s and x['category']=='general']
        self.assertEqual(len(logs),1)
        self.assertIn('原生订单创建不完整',logs[0]['text'])
        self.assertNotIn('mwexFleetLock',e.global_)
        self.assertEqual(s['orders'],[])

    def helper_call(self,e,name,local,inputs,outputs):
        node=ET.Element('run_script',name=repr('mule.fleet.'+name))
        for key in inputs:ET.SubElement(node,'param',name=key,value='$'+key)
        for key,var in outputs.items():ET.SubElement(node,'save_retval',name=key,variable='$'+var)
        e.runner.action(node,local)

    def test_native_missing_return_keeps_the_previous_caller_variable(self):
        e,h,f,hub=self.setup_market([50],[100])
        e.runner.script=lambda name,params:dict(failure='rejected')
        old=Obj(marker='old');local=dict(candidate=old)
        self.helper_call(e,'candidate',local,[],dict(candidate='candidate',failure='reason'))
        self.assertIs(local['candidate'],old)
        self.assertEqual(local['reason'],'rejected')

    def test_successful_candidate_then_price_rejection_clears_the_output(self):
        e,h,f,hub=self.setup_market([50,40],[100]);s,c=f[0]
        local=dict(cfg=c,need=e.needs[0],source=e.sources[0],cache=Obj(),diagnostic=Obj(price=0,minGap=2147483647))
        inputs=('cfg','need','source','cache','diagnostic');outputs=dict(candidate='candidate',failure='failure')
        self.helper_call(e,'candidate',local,inputs,outputs);self.assertTrue(local['candidate'])
        local.update(need=e.needs[1],source=e.sources[1]);e.sources[1]['unitprice']=200
        self.helper_call(e,'candidate',local,inputs,outputs)
        self.assertIsNone(local['candidate']);self.assertIn('价差不满足',local['failure'])

    def test_successful_extension_then_corridor_rejection_clears_the_output(self):
        e,h,f,hub=self.setup_market([50,40],[100]);s,c=f[0];c['corridorDepth']=0
        remote=e.sector('Off corridor');e.connect(h['sector'],remote);e.sources[1]['owner']['sector']=remote
        diagnostic=Obj(price=0,minGap=2147483647,range=0,minExtra=2147483647,route=0);cache=Obj()
        candidates=[e.runner.script('mule.fleet.candidate',dict(cfg=c,need=e.needs[i],source=e.sources[i],cache=cache,diagnostic=diagnostic))['candidate'] for i in (0,1)]
        empty=Obj(plan=[],usedNeed=Obj(),usedSource=Obj(),volume=0,capacity=0,receiver=None,cargoTag=None,
                  budget=s['money'],receiverBudget=0,travel=0,time=1,penalty=0,ownedVolume=0,score=0)
        local=dict(cfg=c,state=empty,candidate=candidates[0],paths=Obj(),distances=Obj(),diagnostic=diagnostic)
        inputs=('cfg','state','candidate','paths','distances','diagnostic');outputs=dict(state='next',failure='failure')
        self.helper_call(e,'extend',local,inputs,outputs);self.assertTrue(local['next'])
        local.update(state=local['next'],candidate=candidates[1])
        self.helper_call(e,'extend',local,inputs,outputs)
        self.assertIsNone(local['next']);self.assertEqual(local['failure'],'corridor')

    def test_rejected_candidates_never_reinsert_an_earlier_ware(self):
        e,h,f,hub=self.setup_market([50,40,30],[100]);s,c=f[0]
        e.sources[1]['unitprice']=200;s['forbidden'].add(e.sources[2]['owner']);e.global_['mwexDebug']=True
        r=self.plan(e,hub,c)
        self.assertEqual(r['diagnostic']['pool'],1);self.assertEqual(r['plan'],[])
        self.assertEqual(len(r['diagnostic']['draft']),1)
        self.assertIs(r['diagnostic']['draft'][0]['ware'],e.wares[0])
        self.assertFalse(any(' CANDIDATE ' in x['text'] and 'accepted=True' in x['text'] and 'reason=ok' not in x['text'].split() for x in e.disk_logs))

    def test_normal_order_preemption_after_commit_cannot_skip_success_finalization(self):
        e,h,f,hub=self.setup_market([50,40],[100]);s,c=f[0];e.global_['mwexDebug']=True
        s['pilot']['mwexFleetAttempt']=Obj(signature=c['signature'],origin=c['origin'],failures=3,next=0,backoff=30)
        def preempt(engine,name,result):
            if name=='mule.fleet.commit' and s['orders']:raise ScriptAborted()
        e.on_script_return=preempt
        with self.assertRaises(ScriptAborted):self.wave(e,hub,s,c)
        self.assertEqual(len(s['orders']),4);self.assertNotIn('mwexFleetLock',e.global_)
        self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],0)
        logs=[x for x in e.logbook if x['object'] is s and x['category']=='general']
        self.assertEqual(len(logs),1);self.assertIn('派单成功',logs[0]['title'])
        self.assertEqual(sum(' RESULT status=success ' in x['text'] for x in e.disk_logs),1)

    def test_commit_has_no_blocking_descendants_even_when_debug_is_enabled(self):
        e,h,f,hub=self.setup_market([50,40],[100]);s,c=f[0];e.global_['mwexDebug']=True
        plan=self.plan(e,hub,c)['plan'];calls=[]
        e.on_script_call=lambda engine,name:calls.append(name)
        result=e.runner.script('mule.fleet.commit',dict(cfg=c,plan=plan))
        self.assertTrue(result['ok']);self.assertEqual(calls,[])
        self.assertNotIn('mwexFleetLock',e.global_)
        self.assertEqual(len(s['orders']),4)

    def test_commit_abort_cleanup_does_not_release_another_transactions_lock(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0]
        own=Obj(actor=s['pilot'],time=0);foreign=Obj(actor=Obj(exists=True),time=1)
        abort=ET.parse(SRC/'aiscripts/mule.fleet.commit.xml').getroot().find('on_abort')
        e.global_['mwexFleetLock']=foreign
        e.runner.children(list(abort),dict(lock=own))
        self.assertIs(e.global_['mwexFleetLock'],foreign)
        e.global_['mwexFleetLock']=own;e.runner.children(list(abort),dict(lock=own))
        self.assertNotIn('mwexFleetLock',e.global_)

    def test_debug_toggle_does_not_add_trade_queries_or_change_dispatch(self):
        results=[]
        for debug in (False,True):
            e,h,f,hub=self.setup_market([50,40],[100]);s,c=f[0];e.global_['mwexDebug']=debug
            self.wave(e,hub,s,c)
            results.append((dict(e.queries),[(o['offer']['owner']['name'],o['offer']['ware']['name'],o['amount']) for o in s['orders']]))
        self.assertEqual(*results)

    def test_empty_legacy_hub_caches_are_reclaimed_without_touching_queued_orders(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0];self.wave(e,hub,s,c)
        orders=list(s['orders']);holds=list(e.global_['mwexFleetPending'])
        oldhome=e.station('Unused old home',h['sector']);old=Obj(home=oldhome,members=Obj(),needs=list(e.needs)*100,sources=Obj(large=True),warnings=Obj(),failures=Obj())
        e.global_['mwexFleetHubs']=Obj({h:hub,oldhome:old})
        actions=ET.parse(SRC/'md/mwex_capacity.xml').getroot().find('./cues/cue[@name="Loaded"]/actions')
        e.runner.children(list(actions),{})
        self.assertEqual(old['needs'],[]);self.assertEqual(old['sources'],{})
        self.assertEqual(s['orders'],orders);self.assertEqual(e.global_['mwexFleetPending'],holds)
        self.assertNotIn('groups',hub);self.assertNotIn('ascending',hub)

    def test_helpers_are_nonblocking_but_large_candidate_loops_still_yield(self):
        e,h,f,hub=self.setup_market([20]*20,[100]);s,c=f[0]
        self.plan(e,hub,c)
        self.assertGreaterEqual(e.waits,1)
        self.assertGreater(e.action_calls['MWEX_Fleet_candidate'],0)
        self.assertFalse(any(name in e.calls for name in ('mule.fleet.candidate','mule.fleet.extend','mule.fleet.trace','mule.fleet.net')))


    def test_bands_send_small_main_and_large_main_to_matching_capacity(self):
        e,h,f,hub=self.setup_market([800,80],[100,1000]);small,sc=f[0];big,bc=f[1]
        a=self.plan(e,hub,sc);b=self.plan(e,hub,bc)
        self.assertIs(a['plan'][0]['ware'],e.wares[1])
        self.assertIs(b['plan'][0]['ware'],e.wares[0])
        self.assertGreater(a['diagnostic']['gradeDeferred'],0)
        self.assertGreater(a['diagnostic']['secondaryAdded'],0)  # small trip may top up across bands

    def test_grouped_lots_admit_large_ship_and_defer_small_main(self):
        e,h,f,hub=self.setup_market([400,400],[100,1000]);small,sc=f[0];big,bc=f[1]
        a=self.plan(e,hub,sc);b=self.plan(e,hub,bc)
        self.assertEqual(a['diagnostic']['status'],'grade_wait')
        self.assertEqual(sum(x['amount'] for x in b['plan']),800)
        self.assertEqual(len(b['plan']),2)

    def test_busy_large_peer_does_not_hold_bulk_for_itself(self):
        e,h,f,hub=self.setup_market([800],[100,1000]);small,sc=f[0]
        f[1][0]['orders'].append(Obj(id='Trade'))
        self.assertEqual(self.plan(e,hub,sc)['plan'][0]['amount'],100)

    def test_band_fallback_at_ninety_seconds_and_three_misses(self):
        for mode in ('time','misses'):
            e,h,f,hub=self.setup_market([800],[100,1000]);s,c=f[0]
            s['pilot']['mwexFleetAttempt']=Obj(waitingSince=0,misses=3 if mode=='misses' else 0)
            e.player['age']=90 if mode=='time' else 0
            self.assertEqual(self.plan(e,hub,c)['plan'][0]['amount'],100)

    def test_same_band_uses_real_capacity_instead_of_model_or_size_label(self):
        e,h,f,hub=self.setup_market([120],[100,140]);s,c=f[0]
        s['size']='XL';s['macro']='different';f[1][0]['size']='M'
        self.assertTrue(self.plan(e,hub,c)['plan'])

    def test_different_receivers_cannot_combine_to_create_large_band_batch(self):
        e,h,f,hub=self.setup_market([400,400],[100,1000]);s,c=f[1]
        other=e.station('Other',h['sector']);e.needs[1]['owner']=other
        for w in e.wares:other['resources'][w]=Obj(exists=True)
        r=self.plan(e,hub,c)
        self.assertFalse(r['plan']);self.assertEqual(r['diagnostic']['status'],'grade_wait')

    def test_wait_age_progressive_load_threshold_and_success_reset(self):
        for elapsed,amount,minimum in ((119,55,70),(120,55,50),(300,30,25),(600,1,0)):
            e,h,f,hub=self.setup_market([amount],[100]);s,c=f[0]
            self.wave(e,hub,s,c);attempt=s['pilot']['mwexFleetAttempt']
            attempt['failures']=0;attempt['waitingSince']=e.player['age']-elapsed
            attempt['next']=0
            e.roots['this']=s['pilot']
            e.runner.script('mule.fleet.wave',dict(hub=hub,cfg=c))
            if elapsed==119:self.assertFalse(s['orders'])
            else:
                self.assertEqual(s['orders'][0]['amount'],amount)
                self.assertIsNone(attempt['waitingSince']);self.assertEqual(attempt['misses'],0)
                self.assertEqual(c['minCargoUsed'],70)

    def test_budget_failure_does_not_restart_wait_age(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0]
        original=e.runner.script
        def exhaust(name,params):
            if name=='mule.fleet.plan':
                r=original(name,params);r['plan']=[];r['diagnostic']['budgetExhausted']=True;r['diagnostic']['status']='budget'
                r['diagnostic']['loadRejected']=False;return r
            return original(name,params)
        e.runner.script=exhaust
        self.wave(e,hub,s,c);started=s['pilot']['mwexFleetAttempt']['waitingSince']
        self.wave(e,hub,s,c)
        self.assertEqual(s['pilot']['mwexFleetAttempt']['waitingSince'],started)
        self.assertEqual(s['pilot']['mwexFleetAttempt']['failures'],0)

    def commit_plan(self,e,hub,c,r):
        return e.runner.script('mule.fleet.commit',dict(cfg=c,hub=hub,plan=r['plan'],diagnostic=r['diagnostic']))

    def test_commit_shrink_keeps_legal_loading_and_reports_actual_quantity(self):
        e,h,f,hub=self.setup_market([90],[100]);s,c=f[0]
        r=self.plan(e,hub,c);e.sources[0]['total']=75
        result=self.commit_plan(e,hub,c,r)
        self.assertTrue(result['ok']);self.assertEqual(s['orders'][0]['amount'],75)
        self.assertEqual(r['diagnostic']['volume'],75);self.assertTrue(r['diagnostic']['repaired'])
        self.assertEqual(s['pilot']['mwexFleetVoyage'][0]['amount'],75)
        self.assertNotIn('mwexFleetLock',e.global_)

    def test_commit_repair_uses_existing_secondary_pool_and_fresh_prices(self):
        e,h,f,hub=self.setup_market([90,50],[100]);s,c=f[0]
        r=self.plan(e,hub,c);self.assertEqual(len(r['plan']),1)
        e.sources[0]['total']=30;e.sources[1]['unitprice']=105
        result=self.commit_plan(e,hub,c,r)
        self.assertTrue(result['ok']);self.assertEqual([x['amount'] for x in result['plan']],[30,50])
        self.assertEqual(result['plan'][1]['buyprice'],105)
        self.assertEqual([x['offer']['isbuyoffer'] for x in s['orders']],[False,False,True,True])

    def test_repair_cannot_use_price_invalid_or_other_receiver_topup(self):
        for invalid in ('price','receiver','corridor'):
            with self.subTest(invalid=invalid):
                e,h,f,hub=self.setup_market([90,50],[100]);s,c=f[0]
                r=self.plan(e,hub,c);e.sources[0]['total']=20
                if invalid=='price':e.sources[1]['unitprice']=120
                if invalid=='receiver':e.needs[1]['owner']=e.station('Other',h['sector'])
                if invalid=='corridor':
                    last=h['sector']
                    for i in range(3):q=e.sector(str(i));e.connect(last,q);last=q
                    e.sources[1]['owner']['sector']=last
                result=self.commit_plan(e,hub,c,r)
                self.assertFalse(result['ok']);self.assertFalse(s['orders']);self.assertNotIn('mwexFleetLock',e.global_)

    def test_repair_deduplicates_shared_need_and_never_overbooks_supply(self):
        e,h,f,hub=self.setup_market([90],[100]);s,c=f[0]
        extra=e.offer(e.station('Extra',h['sector'],'ai'),e.wares[0],50,100,False)
        hub=e.hub(h,f);r=self.plan(e,hub,c);e.sources[0]['total']=30
        result=self.commit_plan(e,hub,c,r)
        self.assertTrue(result['ok'])
        self.assertEqual(sum(x['amount'] for x in result['plan']),80)
        self.assertLessEqual(sum(x['amount'] for x in result['plan'] if x['source'] is extra),50)

    def test_shared_coarse_cache_reused_but_personal_blacklist_is_not_shared(self):
        e,h,f,hub=self.setup_market([1000],[100,100]);a,ac=f[0];b,bc=f[1]
        first=self.plan(e,hub,ac);cached=hub['coarse'][e.wares[0]]
        b['forbidden'].add(e.sources[0]['owner']);second=self.plan(e,hub,bc)
        self.assertIs(hub['coarse'][e.wares[0]],cached)
        self.assertEqual(first['diagnostic']['coarseBuilds'],1)
        self.assertEqual(second['diagnostic']['coarseHits'],1);self.assertFalse(second['plan'])

    def test_discovery_invalidates_coarse_list_and_thirty_second_expiry_refreshes_it(self):
        e,h,f,hub=self.setup_market([100],[100]);s,c=f[0]
        self.plan(e,hub,c);old=hub['coarse'][e.wares[0]]
        e.global_['mwexFleetHubs']=Obj({h:hub});hub['range']=10
        fresh=e.offer(e.station('New',h['sector'],'ai'),e.wares[0],100,99,False)
        self.event(e,fresh);self.plan(e,hub,c)
        self.assertIsNot(hub['coarse'][e.wares[0]],old)
        newer=hub['coarse'][e.wares[0]];e.player['age']+=30
        self.plan(e,hub,c);self.assertIsNot(hub['coarse'][e.wares[0]],newer)

    def test_poll_priority_can_interrupt_movement_but_not_attacked_response(self):
        home=ET.parse(SRC/'aiscripts/mule.fleet.home.xml').getroot()
        call=home.find('./interrupts/handler/actions/run_interrupt_script')
        poll=ET.parse(SRC/'aiscripts/mule.fleet.poll.xml').getroot()
        self.assertEqual(call.get('name'),"'mule.fleet.poll'")
        self.assertEqual(call.get('abortscripts'),'false')
        self.assertGreater(int(poll.get('priority','0')),0)
        self.assertLess(int(poll.get('priority','0')),10)

    def test_stale_return_search_token_does_not_create_orders(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0]
        s['pilot']['mwexFleetReturnSerial']=2
        e.runner.script('mule.fleet.poll',dict(cfg=c,returnToken=1))
        self.assertFalse(s['orders']);self.assertNotIn('mule.fleet.plan',e.calls)

    def test_attacked_response_defers_poll_but_next_timer_still_searches(self):
        e,h,f,hub=self.setup_market([80],[100]);s,c=f[0]
        c=e.runner.script('mule.fleet.config',dict(cfg=c))['cfg'];hub['members'][s]=c
        e.global_['mwexFleetHubs']=Obj({h:hub});s['pilot']['mwexFleetReturnSerial']=1
        actions=ET.parse(SRC/'aiscripts/mule.fleet.home.xml').getroot().find('./interrupts/handler/actions')
        local=dict(cfg=c,hub=hub,ship=s,home=h,returnToken=1,returnStarted=0)
        e.interrupt_priority=10;e.runner.children(list(actions),local)
        self.assertFalse(s['orders']);self.assertEqual(e.signals[-1]['delay'],30)
        e.interrupt_priority=0;e.player['age']+=30;e.runner.children(list(actions),local)
        self.assertEqual(len(s['orders']),2)
        self.assertIn('mwexFleetReturnSearched',s['pilot'])

    def test_repair_extra_queries_are_bounded_when_all_topups_become_invalid(self):
        e,h,f,hub=self.setup_market([90]+[1]*20,[100]);s,c=f[0];e.global_['mwexDebug']=True
        r=self.plan(e,hub,c);e.sources[0]['total']=20
        for source in e.sources[1:]:source['unitprice']=120
        before=e.action_calls['MWEX_Fleet_candidate'];result=self.commit_plan(e,hub,c,r)
        self.assertFalse(result['ok']);self.assertFalse(s['orders'])
        self.assertLessEqual(e.action_calls['MWEX_Fleet_candidate']-before,13)
        repair=next(x['text'] for x in e.disk_logs if ' REPAIR ' in x['text'])
        self.assertIn('extraChecks=12',repair);self.assertIn('added=0',repair)

    def test_wait_fallback_never_relaxes_price_legality(self):
        e,h,f,hub=self.setup_market([90],[100]);s,c=f[0]
        self.wave(e,hub,s,c);s['orders'].clear()
        for offer in e.sources+e.needs:offer.reserved.clear()
        e.global_['mwexFleetPending']=[]
        s['pilot']['mwexFleetAttempt'].update(waitingSince=0,next=0)
        e.player['age']=1000;e.sources[0]['unitprice']=120
        e.runner.script('mule.fleet.wave',dict(hub=hub,cfg=c))
        self.assertFalse(s['orders'])


if __name__=='__main__':
    unittest.main(verbosity=2)
