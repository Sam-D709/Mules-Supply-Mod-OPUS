"""Ownership-neutral M4 regressions against shipping XML, including integer division."""
import unittest
import checks
from checks import Obj


def integer_division(runner):
    """Deliberately truncate every numeric division; not a full native type emulator."""
    evaluate=runner.ev
    def ev(node,local):
        if node[0]=='binary' and node[1]=='/':
            a,b=runner.ev(node[2],local),runner.ev(node[3],local)
            return int(a/b)
        return evaluate(node,local)
    runner.ev=ev


class M4SelectionChecks(unittest.TestCase):
    setup_market=checks.XMLDispatchChecks.setup_market

    def test_small_owned_sources_cannot_hide_full_npc_load_or_full_owned_load(self):
        for swap in (False,True):
            for retired in (False,True):
                for debug in (False,True):
                    e,h,f,hub=self.setup_market([20,20,20,20,1000],[1000]);ship,cfg=f[0]
                    cfg.update(minCargoUsed=75,maxTrades=3,allowPlayerSuppliers=retired)
                    ship['defaultorder'].update(cfg);ship.update(size='XL',iscapitalship=True)
                    for source in e.sources[:4]:source['owner']['owner']='ai' if swap else 'player'
                    e.sources[4]['owner']['owner']='player' if swap else 'ai'
                    e.global_['mwexDebug']=debug
                    e.roots['this']=ship['pilot'];ship['pilot']['ship']=ship
                    result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=False))
                    self.assertEqual(len(result['plan']),1)
                    self.assertIs(result['plan'][0]['source'],e.sources[4])
                    self.assertEqual(result['plan'][0]['amount'],1000)
                    self.assertFalse(result['diagnostic']['candidateBudget'])
                    committed=e.runner.script('mule.fleet.commit',dict(hub=hub,cfg=cfg,
                        plan=result['plan'],diagnostic=result['diagnostic']))
                    self.assertTrue(committed['ok']);self.assertEqual(len(ship['orders']),2)

    def test_fractional_throughput_wins_over_cost_even_below_one(self):
        for integral in (False,True):
            e,h,f,hub=self.setup_market([200,201],[1000]);ship,cfg=f[0]
            ship.update(size='XL',iscapitalship=True)
            cfg.update(maxTrades=1,minCargoUsed=0);e.sources[0]['unitprice']=1
            if integral:integer_division(e.runner)
            result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=False))
            self.assertIs(result['plan'][0]['source'],e.sources[1])
            self.assertEqual(result['diagnostic']['score'],837500)

    def test_large_volume_score_does_not_lose_precision_before_multiplication(self):
        e,h,f,hub=self.setup_market([50000001],[60000000]);ship,cfg=f[0]
        ship.update(size='XL',iscapitalship=True);cfg.update(maxTrades=1,minCargoUsed=0)
        integer_division(e.runner)
        result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=False))
        self.assertEqual(result['plan'][0]['amount'],50000001)
        self.assertEqual(result['diagnostic']['score'],50000001*1000000//240)

    def test_retired_saved_preference_does_not_reset_config_identity(self):
        e,h,f,hub=self.setup_market([80],[100]);ship,cfg=f[0]
        fresh=e.runner.script('mule.fleet.config',dict(cfg=Obj(cfg)))['cfg']
        incoming=Obj(cfg);incoming['allowPlayerSuppliers']=not cfg['allowPlayerSuppliers']
        resolved=e.runner.script('mule.fleet.config',dict(cfg=incoming))['cfg']
        self.assertIs(resolved,fresh);self.assertFalse(resolved['allowPlayerSuppliers'])

    def test_retired_stock_setting_does_not_reset_configuration_identity(self):
        e,h,f,hub=self.setup_market([80],[100]);ship,cfg=f[0]
        fresh=e.runner.script('mule.fleet.config',dict(cfg=Obj(cfg)))['cfg']
        for old in (0,50,100):
            incoming=Obj(cfg);incoming['playerStockThreshold']=old
            self.assertIs(e.runner.script('mule.fleet.config',dict(cfg=incoming))['cfg'],fresh)

    def test_native_sell_limit_and_reservations_bound_owned_pickup(self):
        for debug in (False,True):
            e,h,f,hub=self.setup_market([900],[1000]);ship,cfg=f[0];source=e.sources[0]
            source['owner']['owner']='player'
            source['owner']['cargo'][e.wares[0]].update(count=900,target=1000)
            # Factory physically owns 900, but its native sell offer exposes only 120;
            # 20 of those units are already reserved by another native trader.
            source['total']=120;source.reserved[Obj(id='other')]=20
            cfg.update(playerStockThreshold=100,minCargoUsed=0)
            ship['defaultorder'].update(cfg);e.global_['mwexDebug']=debug
            e.roots['this']=ship['pilot'];ship['pilot']['ship']=ship
            result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
            self.assertEqual(result['plan'][0]['amount'],100)
            self.assertTrue(e.runner.script('mule.fleet.commit',dict(cfg=cfg,plan=result['plan'],
                diagnostic=result['diagnostic'],hub=hub))['ok'])
            self.assertEqual(ship['orders'][0]['amount'],100)

    def test_retired_npc_toggle_is_ignored_by_selection_and_live_commit(self):
        for debug in (False,True):
            e,h,f,hub=self.setup_market([1000],[1000]);ship,cfg=f[0]
            cfg.update(allowAiSuppliers=False,minCargoUsed=75)
            ship['defaultorder'].update(cfg);e.global_['mwexDebug']=debug
            e.roots['this']=ship['pilot'];ship['pilot']['ship']=ship
            result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
            self.assertEqual(result['plan'][0]['amount'],1000)
            # Changing a retired saved field cannot reject a still-legal plan.
            ship['defaultorder']['allowAiSuppliers']=True
            self.assertTrue(e.runner.script('mule.fleet.commit',dict(hub=hub,cfg=cfg,
                plan=result['plan'],diagnostic=result['diagnostic']))['ok'])
            self.assertEqual(len(ship['orders']),2)

    def test_retired_npc_values_share_config_identity_and_upgrade_old_signature(self):
        e,h,f,hub=self.setup_market([100],[100]);ship,cfg=f[0]
        old=checks.Obj(e.runner.script('mule.fleet.config',dict(cfg=checks.Obj(cfg)))['cfg'])
        old['signature']=old['signature'].rsplit('/v',1)[0]+'/v19'
        ship['pilot']['mwexFleetCfg']=old
        incoming=checks.Obj(cfg);incoming['allowAiSuppliers']=False
        fresh=e.runner.script('mule.fleet.config',dict(cfg=incoming))['cfg']
        self.assertIsNot(fresh,old);self.assertTrue(fresh['allowAiSuppliers'])
        for value in (False,True,None):
            incoming=checks.Obj(cfg)
            if value is None:incoming.pop('allowAiSuppliers')
            else:incoming['allowAiSuppliers']=value
            self.assertIs(e.runner.script('mule.fleet.config',dict(cfg=incoming))['cfg'],fresh)

    def test_retired_npc_toggle_does_not_bypass_native_blacklist(self):
        for late in (False,True):
            e,h,f,hub=self.setup_market([100],[100]);ship,cfg=f[0]
            cfg.update(allowAiSuppliers=False)
            if not late:ship['forbidden'].add(e.sources[0]['owner'])
            result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg))
            if late:
                self.assertTrue(result['plan'])
                ship['forbidden'].add(e.sources[0]['owner'])
                self.assertFalse(e.runner.script('mule.fleet.commit',dict(hub=hub,cfg=cfg,
                    plan=result['plan'],diagnostic=result['diagnostic']))['ok'])
            else:self.assertFalse(result['plan'])
            self.assertEqual(e.created,0)


if __name__=='__main__':unittest.main(verbosity=2)
