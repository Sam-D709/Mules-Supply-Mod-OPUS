"""Execute the NEW shipping XML against an explicit, synthetic engine adapter.

This checks the actual control flow and expressions, not a second dispatcher.
The adapter deliberately cannot validate native X4 scheduling, reservation
visibility timing, path weights, docking internals or save serialization.
"""
from collections import deque
from pathlib import Path
import hashlib
import json
import re
import runpy
import sys
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
BINDING_FIELDS = ('mwexFleetBoundOrigin','mwexFleetBoundHome','mwexFleetAssignmentWarning')


class UndefinedBinding(Exception):pass


class Obj(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    def __str__(self): return str(dict.get(self, 'idcode', dict.get(self, 'name', id(self))))
    def __repr__(self): return f'<Obj {str(self)}>'


class Keys(list):
    def __init__(self, table): super().__init__(table); self.table = table


class Proxy:
    def __init__(self, callback, value=0): self.callback, self.value = callback, value


def raw(value): return value.value if isinstance(value, Proxy) else value


def member(value, key):
    key = raw(key)
    if isinstance(value, Proxy): return value.callback(key)
    if isinstance(value, dict):
        if value.get('class') in ('ship','station','sector') and str(key).startswith('mwex'):
            raise AssertionError('Native component has no entity blackboard: '+str(key))
        if key == 'clone': return Obj(value)
        if key == 'keys': return Keys(value)
        if key == 'exists': return value.get(key, True)
        if key == 'isoperational': return value.get(key, value.get('exists', True))
        if key == 'isclass': return Proxy(lambda cls: value.get('class') in cls if isinstance(cls,list) else cls == value.get('class'))
        if key == 'isplayerowned': return value.get('owner') == 'player'
        if key == 'knownname': return value.get('name','')
        if key == 'hasblacklist': return Proxy(lambda typ: Proxy(lambda group: False))
        if key == 'canhavecommander':return Proxy(lambda commander:not value.get('reject_assignment',False))
        if key == 'canuseassignment':return Proxy(lambda assignment:Proxy(lambda commander:not value.get('reject_assignment',False)))
        if isinstance(value, Offer): return value.field(key)
        return value.get(key, 0)
    if isinstance(value, (list, tuple)):
        if key == 'list': return list(value)
        if key == 'count': return len(value)
        if key == 'clone': return list(value)
        if key == 'sorted' and isinstance(value, Keys): return sorted(value, key=lambda x: value.table[x])
        if key == 'min': return min(map(raw, value))
        if key == 'max': return max(map(raw, value))
        if key == 'indexof': return Proxy(lambda item: value.index(item)+1 if item in value else 0)
        if isinstance(key, (int,float)) and int(key)==key:
            return value[int(key)-1] if 0 < key <= len(value) else 0
    if isinstance(value, str) and key == 'id': return value
    if key == 'exists': return False
    return 0


TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(\d+(?:\.\d+)?(?:min|ms|Cr|s)?)(?![\w])|([$A-Za-z_][\w$]*)|(==|!=|[.{}\[\](),?@+*/=<>-]))")


class Parser:
    def __init__(self, text):
        self.tokens=[]; pos=0
        while pos < len(text):
            if not text[pos:].strip(): break
            match=TOKEN.match(text,pos)
            if not match: raise SyntaxError(f'{text!r} near {text[pos:]}')
            self.tokens.append(next(x for x in match.groups() if x is not None));pos=match.end()
        self.tokens.append('<end>');self.i=0
    def peek(self): return self.tokens[self.i]
    def pop(self): value=self.peek();self.i+=1;return value
    def want(self, token):
        value=self.pop()
        if value!=token: raise SyntaxError(f'expected {token}, got {value}; {self.tokens}')
    def parse(self):
        result=self.expr()
        if self.peek()!='<end>':raise SyntaxError(f'Unconsumed: {self.tokens[self.i:]}')
        return result
    def expr(self, minimum=0):
        token=self.pop()
        if token=='if':
            cond=self.expr();self.want('then');yes=self.expr();self.want('else');no=self.expr()
            left=('if',cond,yes,no)
        elif token in ('not','-','@'):left=('unary',token,self.expr(7))
        elif token=='(':
            left=self.expr();self.want(')')
        elif token=='[':
            args=[]
            while self.peek()!=']':
                args.append(self.expr())
                if self.peek()!=',':break
                self.pop()
            self.want(']');left=('list',args)
        elif token=='table':
            self.want('[');pairs=[]
            while self.peek()!=']':
                key=self.pop().lstrip('$');self.want('=');pairs.append((key,self.expr()))
                if self.peek()!=',':break
                self.pop()
            self.want(']');left=('table',pairs)
        elif token=='{':
            page=self.pop();self.want(',');entry=self.pop();self.want('}')
            left=('text',page,entry)
        elif token.startswith("'"):left=('literal',token[1:-1].replace("''", "'"))
        elif token[0].isdigit():
            match=re.fullmatch(r'(\d+(?:\.\d+)?)(min|ms|Cr|s)?',token)
            left=('literal',float(match[1])*{'min':60,'ms':.001,'Cr':1,'s':1,None:1}[match[2]])
        elif token in ('true','false','null'):left=('literal',{'true':True,'false':False,'null':None}[token])
        else:left=('var',token)
        while True:
            token=self.peek()
            if token=='.':
                self.pop()
                if self.peek()=='{':
                    self.pop();key=self.expr();self.want('}');left=('member',left,key)
                elif self.peek()=='[':
                    self.pop();args=[]
                    while self.peek()!=']':
                        args.append(self.expr())
                        if self.peek()!=',':break
                        self.pop()
                    self.want(']');left=('format',left,args)
                else:left=('member',left,('literal',self.pop().lstrip('$')))
                continue
            if token=='?':self.pop();left=('present',left);continue
            if token in ('i','f'):
                self.pop();left=('cast',token,left);continue
            precedence={'or':1,'and':2,'==':3,'!=':3,'gt':3,'lt':3,'ge':3,'le':3,'+':4,'-':4,'*':5,'/':5}.get(token,0)
            if not precedence or precedence<minimum:break
            self.pop();right=self.expr(precedence+1);left=('binary',token,left,right)
        return left


class Done(Exception):
    def __init__(self, values):self.values=values
class Continue(Exception):pass
class Break(Exception):pass
class Resume(Exception):
    def __init__(self,label):self.label=label
class ScriptAborted(Exception):pass


class Runner:
    def __init__(self, engine):self.engine=engine;self.parsed={};self.steps=0;self.action_libraries=None
    def ast(self,text):
        if text not in self.parsed:self.parsed[text]=Parser(text).parse()
        return self.parsed[text]
    def ev(self,node,local):
        tag=node[0]
        if tag=='literal':return node[1]
        if tag=='text':return self.engine.texts.get((node[1],node[2]),f'text:{node[1]}:{node[2]}')
        if tag=='var':
            name=node[1]
            if name.startswith('$'):return local.get(name[1:],0)
            return self.engine.roots.get(name,0)
        if tag=='member':
            target,key=self.ev(node[1],local),self.ev(node[2],local)
            if self.engine.strict_binding_reads and key in BINDING_FIELDS and key not in target:
                raise UndefinedBinding(key)
            return member(target,key)
        if tag=='present':
            child=node[1]
            if child[0]=='member':
                target,key=self.ev(child[1],local),raw(self.ev(child[2],local))
                if isinstance(target,dict):return key in target
            if child[0]=='var' and child[1].startswith('$'):return child[1][1:] in local
            return self.ev(child,local) is not None and self.ev(child,local)!=0
        if tag=='list':return [self.ev(x,local) for x in node[1]]
        if tag=='table':return Obj({key:self.ev(value,local) for key,value in node[1]})
        if tag=='if':return self.ev(node[2] if raw(self.ev(node[1],local)) else node[3],local)
        if tag=='cast':return (int if node[1]=='i' else float)(raw(self.ev(node[2],local)))
        if tag=='unary':
            try:value=raw(self.ev(node[2],local))
            except UndefinedBinding:
                if node[1]=='@':return None
                raise
            return not value if node[1]=='not' else -value if node[1]=='-' else value
        if tag=='format':
            text=self.ev(node[1],local);args=tuple(str(raw(self.ev(x,local))) for x in node[2])
            return text % args[:text.count('%s')] if '%s' in text else text+' '+str(args)
        if tag=='binary':
            op=node[1];a=raw(self.ev(node[2],local))
            # Conservative binding checks must also work if BOTH boolean operands
            # are evaluated. This is a stress mode, not a full native evaluator.
            if self.engine.strict_binding_reads and op in ('and','or') and any(k in repr(node) for k in BINDING_FIELDS):
                b=raw(self.ev(node[3],local));return (a and b) if op=='and' else (a or b)
            if op=='and':return a and raw(self.ev(node[3],local))
            if op=='or':return a or raw(self.ev(node[3],local))
            b=raw(self.ev(node[3],local))
            if op=='+':return a+b
            if op=='-':return a-b
            if op=='*':return a*b
            if op=='/':return a/b
            if op=='==':return a==b
            if op=='!=':return a!=b
            if op=='gt':return a>b
            if op=='lt':return a<b
            if op=='ge':return a>=b
            if op=='le':return a<=b
        raise ValueError(node)
    def value(self,text,local):return self.ev(self.ast(text),local)
    def set(self,path,value,local,remove=False,operation=None):
        node=self.ast(path)
        if node[0]=='var':target,key=local,node[1][1:]
        else:
            assert node[0]=='member',node
            target=self.ev(node[1],local);key=raw(self.ev(node[2],local))
            if node[2] in (('var','$group'),('var','$groupKey'),('var','$failureKey')) and isinstance(key,str) and not key.startswith('$'):
                raise AssertionError('Native table string keys must start with $: '+key)
        if isinstance(target,dict) and target.get('class') in ('ship','station','sector') and str(key).startswith('mwex'):
            raise AssertionError('Native component cannot store entity blackboard: '+str(key))
        if isinstance(target,list):
            # X4 list access and writes both use one-based indices.
            assert isinstance(key,(int,float)) and int(key)==key and 1<=key<=len(target), key
            key=int(key)-1
            if remove:target.pop(key);return
            if operation=='add':value=target[key]+value
            if operation=='subtract':value=target[key]-value
            target[key]=value;return
        if remove:target.pop(key,None);return
        if operation=='add':value=target.get(key,0)+value
        if operation=='subtract':value=target.get(key,0)-value
        target[key]=value
    def script(self,name,params):
        self.engine.calls[name]=self.engine.calls.get(name,0)+1
        root=ET.parse(SRC/'aiscripts'/('supply_mule.xml' if name=='supplymule' else f'{name}.xml')).getroot();local=dict(params)
        for p in root.findall('./params/param'):
            if p.get('name') not in local:local[p.get('name')]=self.value(p.get('default','null'),local)
        actions=root.find('./attention/actions')
        children=list(actions);labels={c.get('name'):i for i,c in enumerate(children) if c.tag=='label'}
        start=0
        try:
            if root.find('init') is not None:self.children(list(root.find('init')),local)
            while True:
                try:self.children(children[start:],local);break
                except Resume as resumed:start=labels[resumed.label]+1
        except Done as done:return done.values
        except ScriptAborted:
            cleanup=root.find('on_abort')
            if cleanup is not None:self.children(list(cleanup),local)
            raise
        return {}
    def children(self,children,local):
        i=0
        while i<len(children):
            node=children[i]
            if node.tag=='do_if':
                group=[node];i+=1
                while i<len(children) and children[i].tag in ('do_elseif','do_else'):
                    group.append(children[i]);i+=1
                for branch in group:
                    if branch.tag=='do_else' or self.value(branch.get('value'),local):
                        self.children(list(branch),local);break
                continue
            self.action(node,local);i+=1
    def action(self,node,local):
        self.steps+=1
        if self.steps>2000000:raise RuntimeError('Synthetic adapter step limit')
        tag=node.tag
        v=lambda key,default='null':self.value(node.get(key,default),local)
        if tag=='set_value':self.set(node.get('name'),v('exact'),local,operation=node.get('operation'));return
        if tag=='remove_value':self.set(node.get('name'),None,local,remove=True);return
        if tag=='append_to_list':
            target=v('name');target.extend(v('list')) if node.get('list') else target.append(v('exact'));return
        if tag=='remove_from_list':
            target=v('name');remove=v('list') if node.get('list') else [v('exact')]
            for item in list(remove):
                if item in target:target.remove(item)
            return
        if tag in ('do_for_each','do_all'):
            if tag=='do_all':items=[(i,i) for i in range(1,int(v('exact'))+1)]
            else:
                source=v('in');items=list(source.items()) if isinstance(source,dict) else [(x,x) for x in list(source)]
            if node.get('reverse')=='true':items.reverse()
            for index,(key,value) in enumerate(items,1):
                if node.get('name'):self.set(node.get('name'),key,local)
                if node.get('valuename'):self.set(node.get('valuename'),value,local)
                if node.get('counter'):self.set(node.get('counter'),key if tag=='do_all' else index,local)
                try:self.children(list(node),local)
                except Continue:continue
                except Break:break
            return
        if tag=='continue':raise Continue()
        if tag=='break':raise Break()
        if tag=='return':raise Done({c.get('name'):self.value(c.get('value'),local) for c in node})
        if tag=='resume':raise Resume(node.get('label'))
        if tag=='include_interrupt_actions':
            self.engine.action_calls[node.get('ref')]=self.engine.action_calls.get(node.get('ref'),0)+1
            if self.action_libraries is None:
                self.action_libraries={}
                for path in (SRC/'aiscripts').glob('*.xml'):
                    for group in ET.parse(path).getroot().findall('./interrupts/library/actions'):
                        name=group.get('name')
                        if name in self.action_libraries:raise AssertionError('Duplicate action library: '+name)
                        self.action_libraries[name]=group
            self.children(list(self.action_libraries[node.get('ref')]),local)
            return
        if tag in ('run_script','run_interrupt_script'):
            if 'mwexFleetLock' in self.engine.global_:
                raise AssertionError('Blocking script call holds the dispatch lock: '+str(v('name')))
            if self.engine.on_script_call:self.engine.on_script_call(self.engine,v('name'))
            if v('name')=='move.generic':
                params={c.get('name'):self.value(c.get('value'),local) for c in node if c.tag=='param'}
                # Model completion/interruption only; native flying and path following require in-game testing.
                self.engine.moves.append(params)
                if 'mwexFleetLock' in self.engine.global_:raise AssertionError('Movement holds the dispatch lock')
                if self.engine.on_move:self.engine.on_move(self.engine,params)
                else:
                    ship=self.engine.roots['this']['ship'];destination=params['destination']
                    ship['sector']=destination['sector'];ship['zone']=destination.get('zone',destination)
                return
            previous_priority=getattr(self.engine,'interrupt_priority',0)
            if tag=='run_interrupt_script':
                target_root=ET.parse(SRC/'aiscripts'/f"{v('name')}.xml").getroot()
                priority=int(target_root.get('priority','0'))
                if priority<=previous_priority:return
                self.engine.interrupt_priority=priority
            try:
                result=self.script(v('name'),{c.get('name'):self.value(c.get('value'),local) for c in node if c.tag=='param'})
            finally:
                if tag=='run_interrupt_script':self.engine.interrupt_priority=previous_priority
            if self.engine.on_script_return:self.engine.on_script_return(self.engine,v('name'),result)
            for c in node:
                # Native callers keep their previous variable when this return name is omitted.
                if c.tag=='save_retval' and c.get('name') in result:self.set(c.get('variable'),result[c.get('name')],local)
            return
        if tag=='find_zone':
            self.set(node.get('name'),v('space').get('returnzone'),local);return
        if tag=='wait':
            self.engine.player['age']+=v('exact',node.get('min','0'));self.engine.waits+=1
            if self.engine.on_wait:self.engine.on_wait(self.engine)
            return
        if tag in ('debug_to_file','debug_text'):
            text=v('text');self.engine.logs.append(text)
            if tag=='debug_to_file':self.engine.disk_logs.append(Obj(name=v('name'),directory=v('directory'),text=text))
            return
        if tag=='signal_cue_instantly':
            self.engine.cue_signals.append(Obj(cue=v('cue'),param=v('param')));return
        if tag=='write_to_logbook':
            text=v('text');self.engine.notices.append(text)
            self.engine.logbook.append(Obj(title=v('title'),text=text,object=v('object'),category=node.get('category')))
            return
        if tag=='set_order_failed':
            order=v('order');order['failure']=v('text');self.engine.ship_failures.append((order,v('text')));return
        if tag=='clear_order_failure':v('order').pop('failure',None);return
        if tag=='reward_player':self.engine.player['money']+=v('money');return
        if tag=='remove_cargo':
            ship,ware=v('object'),v('ware');amount=min(int(v('exact')),ship['cargo'][ware]['count'])
            ship['cargo'][ware]['count']-=amount
            ship['cargo']['free']['all']+=amount*ware['volume'];ship['cargo'][ware]['free']+=amount
            ship['cargo']['list']=[w for w in ship['cargo']['list'] if ship['cargo'][w]['count']>0]
            ship['cargo']['count']=sum(ship['cargo'][w]['count'] for w in self.engine.wares)
            return
        if tag in ('set_order_syncpoint_reached','set_command_action'):return
        if tag=='get_factions_by_relation':self.set(node.get('result'),['player','ai'],local);return
        if tag=='set_object_commander':
            ship=v('object')
            self.engine.assignments.append((ship,v('commander')))
            if not ship.get('reject_assignment',False):ship['commander']=v('commander');ship['toplevelcommander']=v('commander')
            return
        if tag=='signal_objects':
            self.engine.signals.append(Obj(object=v('object'),param=v('param'),param2=v('param2'),delay=v('delay','0s')));return
        if tag=='label':return
        if tag=='get_ware_definition':self.set(node.get('result'),self.engine.wares,local);return
        if tag in ('find_buy_offer','find_sell_offer'):
            self.engine.queries[tag]=self.engine.queries.get(tag,0)+1
            pool=self.engine.needs if tag=='find_buy_offer' else self.engine.sources
            owner=v('buyer' if tag=='find_buy_offer' else 'seller');wares=v('wares');ship=v('tradepartner')
            matches=[]
            for offer in pool:
                if owner and offer['owner'] is not owner:continue
                if wares and offer['ware'] not in (wares if isinstance(wares,list) else [wares]):continue
                if not offer.get('exists',True):continue
                if node.get('excludeempty')!='false' and offer.field('amount')<=0:continue
                if ship and not offer.allowed(ship):continue
                price_filter=node.find('price')
                if price_filter is not None and offer.actual_price(ship)>self.value(price_filter.get('max'),local)+1e-8:continue
                matches.append(offer)
            self.set(node.get('result'),matches,local);return
        if tag=='find_closest_dockingbay':
            owner,ship=v('object'),v('ship')
            self.set(node.get('name'),Obj(name='dock') if ship['size'] in owner['docks'] else None,local);return
        if tag=='get_global_path':
            start=self.value(node.find('start').get('object'),local);end=self.value(node.find('end').get('object'),local)
            ship=v('refobject');path=self.engine.path(start.get('sector',start),end['sector'],ship)
            self.set(node.get('component'),path or [],local)
            if node.get('resultdistance'):self.set(node.get('resultdistance'),len(path)-1 if path else -1,local)
            return
        if tag=='clamp_trade_amount':
            offer,amount,buyer,seller=v('trade'),int(v('amount')),v('buyer'),v('seller')
            if buyer:
                if buyer['class']=='ship':amount=min(amount,member(member(buyer['cargo'],offer['ware']),'free'))
                price=offer.actual_price(buyer)
                if not offer.field('buyfree').callback(buyer) and price>0:amount=min(amount,int(buyer['money']//price))
            self.set(node.get('result'),max(0,amount),local);return
        if tag=='create_trade_order':
            order=self.engine.order(v('object'),v('tradeoffer'),int(v('amount')))
            if node.get('name'):self.set(node.get('name'),order,local)
            return
        if tag=='cancel_order':self.engine.cancel(v('order'));return
        raise NotImplementedError(tag)


class Offer(Obj):
    def __init__(self,owner,ware,amount,price,buy):
        super().__init__(owner=owner,ware=ware,total=amount,unitprice=price,isbuyoffer=buy,
                         minamount=1,exists=True);self.reserved={}
    def allowed(self,ship):return (self['owner']['sector'] not in ship['blocked'] and
                                  self['owner'] not in ship['forbidden'] and
                                  self['ware'] in ship['cargo'])
    def actual_price(self,ship):return self.get('actualprice',self['unitprice'])
    def field(self,key):
        if key=='amount':return max(0,self['total']-sum(self.reserved.values()))
        if key=='desiredamount':return self['total']
        if key=='offeramount':return Proxy(lambda ship:self.field('amount')+self.reserved.get(ship,0),self['total'])
        if key=='available':return self.get('exists',True) and self.field('amount')>0
        if key=='cantradewith':return Proxy(self.allowed)
        if key in ('buyer','seller'):return self['owner'] if (key=='buyer')==self['isbuyoffer'] else None
        if key=='buyfree':return Proxy(lambda buyer: (self['owner']['owner']==buyer.get('owner')
                                                    if not self['isbuyoffer'] else False),False)
        return dict.get(self,key,0)


class Engine:
    def __init__(self,delayed=False,language='zh'):
        self.player=Obj(age=0,money=10**12,occupiedship=None,entity=Obj(name='player'),galaxy=Obj(name='galaxy'))
        self.modifier_targets=set()
        self.player['hasdiscount']=Proxy(lambda target:target in self.modifier_targets)
        self.player['hascommission']=Proxy(lambda target:target in self.modifier_targets)
        text_file=SRC/'t'/('0001.xml' if language=='en' else '0001-l086.xml')
        self.texts={('61552',n.get('id')):n.text for n in ET.parse(text_file).getroot().iter('t')}
        self.global_=Obj(mwexFleetPending=[],mwexFleetEpoch=0)
        self.roots={'global':self.global_,'player':self.player,
                    'faction':Obj(player='player'),'class':Obj(ship='ship',station='station',sector='sector'),
                    'assignment':Obj(trade='trade'),'unitcategory':Obj(transport='transport'),'blacklistgroup':Obj(civilian='civilian'),
                    'blacklisttype':Obj(sectortravel='travel',sectoractivity='activity',objectactivity='object')}
        self.wares=[];self.needs=[];self.sources=[];self.logs=[];self.notices=[];self.waits=0
        self.calls={};self.action_calls={};self.disk_logs=[];self.cue_signals=[]
        self.delayed=delayed;self.created=0;self.fail_at=None;self.graph={};self.runner=Runner(self)
        self.on_wait=None;self.queries={};self.partial_at=None;self.running_partial=False
        self.on_script_call=None;self.on_script_return=None
        self.strict_binding_reads=True
        self.assignments=[]
        self.moves=[];self.on_move=None;self.ship_failures=[];self.signals=[];self.logbook=[]
    def sector(self,name):
        s=Obj(name=name,**{'class':'sector'});s['sector']=s
        s['returnzone']=Obj(name=name+' navigation zone',sector=s,**{'class':'zone'})
        def distance(other):
            def with_ship(ship):
                path=self.path(s,other,ship);return len(path)-1 if path else -1
            return Proxy(lambda group:Proxy(with_ship),len(self.path(s,other,None) or [])-1)
        s['gatedistance']=Proxy(distance);self.graph[s]=[];return s
    def connect(self,a,b):self.graph[a].append(b);self.graph[b].append(a)
    def path(self,a,b,ship):
        blocked=ship.get('blocked',set()) if ship else set()
        if a in blocked or b in blocked:return None
        todo=deque([(a,[a])]);seen={a}
        while todo:
            sector,path=todo.popleft()
            if sector is b:return path
            for neighbor in self.graph[sector]:
                if neighbor not in seen and neighbor not in blocked:
                    seen.add(neighbor);todo.append((neighbor,path+[neighbor]))
        return None
    def ware(self,name,volume=1,tag='container'):
        w=Obj(id=name,name=name,volume=volume,transporttag=tag,averageprice=100,minprice=50,maxprice=200,pricerange=150);self.wares.append(w);return w
    def station(self,name,sector,owner='player',docks=('S','M','L','XL')):
        obj=Obj(name=name,idcode=name,sector=sector,zone=Obj(name=name+' zone'),owner=owner,money=10**12,
                units={'transport':{'count':1}},resources=Obj(),supplyresources=Obj(),
                products=Obj(),tradewares=Obj(),cargo=Obj(free=Obj(all=10**12,container=10**12)),docks=docks,allsubordinates=[],**{'class':'station'})
        return obj
    def offer(self,owner,ware,amount,price,buy):
        offer=Offer(owner,ware,amount,price,buy)
        (self.needs if buy else self.sources).append(offer)
        owner['resources'][ware]=Obj(exists=buy);owner['products'][ware]=Obj(exists=not buy)
        owner['tradewares'][ware]=Obj(exists=True);owner['cargo'][ware]=Obj(count=0 if buy else amount,target=max(1,amount))
        return offer
    def ship(self,name,capacity,home,macro=None,size='M',**settings):
        cargo=Obj(count=0,list=[],capacity=Obj(all=capacity,container=capacity),free=Obj(all=capacity,container=capacity))
        for w in self.wares:cargo[w]=Obj(max=int(capacity//w['volume']),free=int(capacity//w['volume']),count=0)
        ship=Obj(name=name,idcode=name,cargo=cargo,money=10**12,macro=macro or name,sector=home['sector'],zone=home['zone'],
                 owner='player',orders=[],subordinates=[],isoperational=True,iscapitalship=size in ('L','XL'),size=size,
                 waretransport=['container'],units={'transport':{'count':1}},blocked=set(),forbidden=set(),
                 pilot=Obj(exists=True),commander=home,toplevelcommander=home,**{'class':'ship'})
        cfg=Obj(ship=ship,home=home,maxDist=10,dedicatedServe=True,allowPlayerSuppliers=True,allowAiSuppliers=True,
                allowBuildstorage=True,allowResources=True,allowIntermediates=True,allowTradewares=True,
                lockWares=False,wares=[],maxTrades=3,minCargoUsed=70,playerBuyMod=100,searchDepth=100,playerStockThreshold=50,
                corridorDepth=2,corridorMarkup=5,commander=home,bound=True,signature='same')
        cfg.update(settings);origin=Obj(id='SupplyMule',isrunning=True);origin.update(cfg);origin['specialWareBasket']=cfg['wares'];cfg['origin']=origin
        ship['defaultorder']=origin;ship['pilot']['mwexFleetCfg']=cfg;home['allsubordinates'].append(ship)
        return ship,cfg
    def hub(self,home,ships):
        hub=Obj(home=home,members=Obj(),groups=Obj(),capacities=Obj(),warnings=Obj(),failures=Obj(),
                needs=list(self.needs),sources=Obj(),cacheDirty=False,cacheUntil=10**9,cacheEpoch=0,
                backoff=2,next=0,rosterDirty=False)
        for ship,cfg in ships:
            key='$model/'+str(ship['macro'])+'/'+str(ship['cargo']['capacity']['all'])
            hub['members'][ship]=cfg;hub['groups'].setdefault(key,[]).append(ship)
            hub['capacities'][key]=ship['cargo']['capacity']['all']
        hub['ascending']=sorted(hub['groups'],key=lambda x:hub['capacities'][x]);hub['descending']=list(reversed(hub['ascending']))
        for offer in self.sources:hub['sources'].setdefault(offer['ware'],Obj()).setdefault(offer['owner']['sector'],Obj())[offer['owner']]=offer
        return hub
    def order(self,ship,offer,amount):
        self.created+=1
        if self.fail_at==self.created:return None
        if self.partial_at==self.created:amount=max(1,amount//2)
        order=Obj(id='Trade',state='queued',isrunning=False,trade=Obj(amount=amount,transferredamount=0),
                  ship=ship,offer=offer,amount=amount)
        if self.partial_at==self.created and self.running_partial:order['isrunning']=True
        ship['orders'].append(order)
        if not self.delayed:offer.reserved[ship]=offer.reserved.get(ship,0)+amount
        return order
    def cancel(self,order):
        if not order or not order.get('exists',True):return
        ship,offer,amount=order['ship'],order['offer'],order['amount']
        if order in ship['orders']:ship['orders'].remove(order)
        order['exists']=False
        if ship in offer.reserved:offer.reserved[ship]=max(0,offer.reserved[ship]-amount)


class XMLDispatchChecks(unittest.TestCase):
    def assignment_notices(self,e):
        titles={e.texts[('61552','4340')],e.texts[('61552','4342')]}
        return [entry['text'] for entry in e.logbook if entry['title'] in titles]

    def entry(self,e,ship,cfg,home):
        local=dict(cfg,home=home,assignHome=True,specialWareBasket=[],restartScript=0)
        ship['defaultorder']['home']=home;ship['defaultorder']['assignHome']=True
        e.roots['this']=Obj(ship=ship,name='pilot',assignedcontrolled=ship)
        e.global_.pop('mwexFleetLock',None)
        e.runner.script('supplymule',local)

    def test_original_m4_entry_assigns_station_and_creates_trade_orders(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        ship['commander']=None;ship['pilot'].pop('mwexFleetCfg')
        self.entry(e,ship,cfg,home)
        self.assertIs(ship['commander'],home)
        self.assertEqual(len(ship['orders']),2)
        self.assertIs(e.global_['mwexFleetHubs'][home]['members'][ship],ship['pilot']['mwexFleetCfg'])

    def test_original_m4_sector_home_warns_but_still_dispatches(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        ship['commander']=None;ship['pilot'].pop('mwexFleetCfg')
        self.entry(e,ship,cfg,home['sector'])
        self.assertIsNone(ship['commander'])
        self.assertEqual(len(ship['orders']),2)
        self.assertEqual(len(self.assignment_notices(e)),1)

    def test_native_blackboard_registration_keeps_fresh_member_and_dispatches(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        ship['pilot'].pop('mwexFleetCfg');hub['members'].clear()
        hub['groups'].clear();hub['capacities'].clear();hub['ascending']=[];hub['descending']=[]
        self.run_roster(e,home,fleet,hub)
        self.assertIs(ship['pilot']['mwexFleetCfg'],hub['members'][ship])
        self.assertEqual(len(ship['orders']),2)

    def test_rejected_assignment_records_native_conditions_and_does_not_block_trade(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        ship.update(commander=None,reject_assignment=True)
        ship['pilot'].pop('mwexFleetCfg')
        self.entry(e,ship,cfg,home)
        diag=ship['pilot']['mwexFleetAssignmentCheck']
        self.assertFalse(diag['canHave']);self.assertFalse(diag['canTrade'])
        self.assertIsNone(diag['after']);self.assertNotIn('mwexFleetBoundOrigin',ship['pilot'])
        self.assertEqual(len(self.assignment_notices(e)),1);self.assertEqual(len(ship['orders']),2)

    def test_idle_registration_returns_to_refresh_within_eight_seconds(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        e.roots['this']=Obj(ship=ship,name='pilot')
        e.global_.pop('mwexFleetLock',None)
        e.runner.script('mule.fleet.run',{'cfg':cfg})
        self.assertGreater(e.player['age'],5);self.assertLessEqual(e.player['age'],8)

    def test_live_sector_home_edit_migrates_hub_and_searches_new_sector(self):
        for demand in (0,80):
            with self.subTest(new_demand=demand):
                e,home,fleet,_=self.setup_market([80],[100]);ship,cfg=fleet[0]
                old=home['sector'];new=e.sector('New home');e.connect(old,new)
                cfg.update(home=old,bound=False,maxDist=0,playerStockThreshold=50.0)
                cfg['origin'].update(home=old,maxDist=0,playerStockThreshold=50.0)
                hub=e.hub(old,fleet);e.global_['mwexFleetHubs']=Obj({old:hub})
                e.global_.pop('mwexFleetLock',None)
                receiver=e.station('New receiver',new);source=e.station('New source',new,'ai')
                need=e.offer(receiver,e.wares[0],demand,110,True)
                e.offer(source,e.wares[0],demand,100,False)
                # The UI edits the existing order; the running script still holds its old snapshot.
                cfg['origin']['home']=new
                e.roots['this']=Obj(ship=ship,name='pilot')
                e.runner.script('mule.fleet.run',{'cfg':cfg})
                self.assertNotIn(ship,hub['members'])
                self.assertIs(cfg['home'],old,'Do not mutate the old shared configuration')
                fresh=ship['pilot']['mwexFleetCfg']
                self.assertIs(fresh['home'],new)
                self.assertIs(e.global_['mwexFleetHubs'][new]['members'][ship],fresh)
                self.assertIs(ship['sector'],old if demand else new)
                if demand:
                    self.assertEqual(len(ship['orders']),2)
                    self.assertIs(ship['orders'][1]['offer'],need)
                else:
                    self.assertIs(e.moves[0]['destination'],new['returnzone'])

    def test_old_home_wave_does_not_plan_for_ship_with_edited_home(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        new=e.sector('New home');e.connect(home['sector'],new)
        cfg['origin']['home']=new
        self.dispatch(e,hub)
        self.assertEqual(ship['orders'],[])
        self.assertFalse(any(line.startswith('CANDIDATE') for line in e.logs))

    def test_old_home_return_stops_after_live_home_edit(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        new=e.sector('New home');e.connect(home['sector'],new)
        ship['zone']=Obj(name='away from station')
        cfg['origin']['home']=new
        self.return_home(e,hub,ship,cfg)
        self.assertEqual(e.moves,[])

    def test_mimic_home_is_not_replaced_by_leader_order_home(self):
        e,home,fleet,_=self.setup_market([0],[100]);ship,cfg=fleet[0]
        cfg['origin']['id']='Assist';cfg['origin']['home']=e.sector('Other')
        fresh=e.runner.script('mule.fleet.config',{'cfg':cfg})['cfg']
        self.assertIs(fresh['home'],home)

    def test_home_edit_during_idle_is_seen_before_another_old_home_search(self):
        e,home,fleet,_=self.setup_market([0],[100]);ship,cfg=fleet[0]
        old=home['sector'];new=e.sector('New home');e.connect(old,new)
        cfg.update(home=old,bound=False);cfg['origin']['home']=old
        hub=e.hub(old,fleet);e.global_['mwexFleetHubs']=Obj({old:hub})
        e.global_.pop('mwexFleetLock',None)
        e.roots['this']=Obj(ship=ship,name='pilot')
        def edit_home(engine):
            cfg['origin']['home']=new;engine.on_wait=None
        e.on_wait=edit_home
        e.runner.script('mule.fleet.run',{'cfg':cfg})
        self.assertEqual(e.moves,[])
        e.runner.script('mule.fleet.run',{'cfg':cfg})
        self.assertIs(e.moves[0]['destination'],new['returnzone'])
        self.assertNotIn(ship,hub['members'])

    def test_home_migration_keeps_already_queued_trade_and_reservations(self):
        e,home,fleet,_=self.setup_market([80],[100]);ship,cfg=fleet[0]
        old=home['sector'];new=e.sector('New home');e.connect(old,new)
        cfg.update(home=old,bound=False);cfg['origin']['home']=new
        hub=e.hub(old,fleet);e.global_['mwexFleetHubs']=Obj({old:hub})
        e.global_.pop('mwexFleetLock',None)
        order=e.order(ship,e.sources[0],40);pending=Obj(ship=ship,offer=e.sources[0],order=order,amount=40)
        e.global_['mwexFleetPending'].append(pending)
        e.roots['this']=Obj(ship=ship,name='pilot')
        e.runner.script('mule.fleet.run',{'cfg':cfg})
        self.assertEqual(ship['orders'],[order]);self.assertIn(pending,e.global_['mwexFleetPending'])
        self.assertNotIn(ship,hub['members']);self.assertEqual(e.moves,[])

    def test_direct_entry_reads_edited_home_even_with_stale_script_parameter(self):
        e,home,fleet,_=self.setup_market([0],[100]);ship,cfg=fleet[0]
        new=e.sector('New home');e.connect(home['sector'],new)
        ship['defaultorder']['home']=new
        local=dict(cfg,assignHome=False,specialWareBasket=[],restartScript=0)
        e.roots['this']=Obj(ship=ship,name='pilot',assignedcontrolled=ship)
        e.global_.pop('mwexFleetLock',None)
        e.runner.script('supplymule',local)
        self.assertIs(ship['pilot']['mwexFleetCfg']['home'],new)
        self.assertIs(e.moves[0]['destination'],new['returnzone'])

    def test_legacy_binding_guard_reads_missing_state_under_eager_validation(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        ship['commander']=None
        with self.assertRaises(UndefinedBinding):
            e.runner.value('not @$ship.pilot.$mwexFleetBoundOrigin? or $ship.pilot.$mwexFleetBoundOrigin != $ship.defaultorder',{'ship':ship})

    def test_direct_registration_binds_fresh_ship_and_preserves_trade_queue(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        ship['commander']=None;ship['defaultorder']['assignHome']=True
        order=e.order(ship,e.sources[0],1)
        self.run_roster(e,home,fleet,hub)
        self.assertIs(ship['commander'],home);self.assertEqual(ship['orders'],[order])
        self.assertEqual(len(e.assignments),1)
        diag=ship['pilot']['mwexFleetAssignmentCheck']
        self.assertTrue(diag['attempted']);self.assertEqual(diag['reason'],'first-binding')
        self.assertIs(ship['pilot']['mwexFleetCfg']['commander'],home)

    def test_reload_already_assigned_does_not_repeat_assignment(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        ship['defaultorder']['assignHome']=True
        self.run_roster(e,home,fleet,hub)
        self.assertEqual(e.assignments,[])
        self.assertIs(ship['pilot']['mwexFleetBoundOrigin'],ship['defaultorder'])
        self.assertEqual(ship['pilot']['mwexFleetAssignmentCheck']['reason'],'already-assigned')

    def test_manually_detached_same_binding_is_not_forced_back(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        ship['defaultorder']['assignHome']=True
        self.run_roster(e,home,fleet,hub)
        ship['commander']=None
        self.run_roster(e,home,fleet,hub)
        self.assertIsNone(ship['commander']);self.assertEqual(e.assignments,[])
        self.assertEqual(ship['pilot']['mwexFleetAssignmentCheck']['reason'],'same-binding-detached')

    def test_changed_binding_and_incomplete_binding_can_assign(self):
        for case in ('order','home','missing-home'):
            with self.subTest(case=case):
                e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
                ship['commander']=None;ship['defaultorder']['assignHome']=True
                pilot=ship['pilot'];pilot['mwexFleetBoundOrigin']=ship['defaultorder']
                if case=='order':pilot['mwexFleetBoundOrigin']=Obj(id='previous-order')
                if case!='missing-home':pilot['mwexFleetBoundHome']=Obj(name='old-home')
                self.run_roster(e,home,fleet,hub)
                self.assertIs(ship['commander'],home);self.assertEqual(len(e.assignments),1)

    def setup_market(self,amounts,capacities,delayed=False,language='zh'):
        e=Engine(delayed,language);h=e.sector('H');home=e.station('Home',h)
        for i,amount in enumerate(amounts):
            w=e.ware('w'+str(i));supplier=e.station('Source'+str(i),h,'ai')
            e.offer(home,w,amount,110,True);e.offer(supplier,w,amount,100,False)
        fleet=[e.ship('ship'+str(i),cap,home,macro='model'+str(cap)) for i,cap in enumerate(capacities)]
        return e,home,fleet,e.hub(home,fleet)
    def dispatch(self,e,hub):
        e.player['age']+=31
        e.global_.pop('mwexFleetLock',None)
        for attempt in range(7):
            for ship,cfg in list(hub['members'].items()):
                e.roots['this']=ship['pilot'];e.roots['this']['ship']=ship
                e.runner.script('mule.fleet.wave',{'hub':hub,'cfg':cfg})
            if not any(s['pilot'].get('mwexFleetAttempt',{}).get('lastStatus') in ('load','grade_wait') and not s['orders'] for s,c in hub['members'].items()):break
            e.player['age']+=31
    def test_manual_trade_stock_ignores_retired_flag_but_requires_buy_demand(self):
        for amount in (0,80):
            with self.subTest(amount=amount):
                e,home,fleet,hub=self.setup_market([amount],[100]);ware=e.wares[0]
                home['resources'][ware]['exists']=False
                ship,cfg=fleet[0]
                cfg.update(allowResources=False,allowIntermediates=False,allowBuildstorage=False,allowTradewares=False)
                ship['defaultorder'].update(cfg)
                self.dispatch(e,hub)
                self.assertEqual(len(ship['orders']),2 if amount else 0)
                if amount:self.assertEqual(ship['orders'][0]['amount'],amount)

    def test_remaining_ware_switches_still_filter_receiver_uses(self):
        for category,flag in (('resource','allowResources'),('intermediate','allowIntermediates'),('build','allowBuildstorage')):
            with self.subTest(category=category):
                e,home,fleet,hub=self.setup_market([80],[100]);ware=e.wares[0];ship,cfg=fleet[0]
                if category=='intermediate':home['products'][ware]['exists']=True
                if category=='build':
                    store=e.station('Build',home['sector']);store.update({'class':'buildstorage','base':home})
                    e.needs[0]['owner']=store
                cfg[flag]=False;ship['defaultorder'][flag]=False
                self.dispatch(e,hub)
                self.assertEqual(ship['orders'],[])

    def test_buildstorage_only_keeps_player_price_exception_without_retired_flag(self):
        for old_flag in (False,True):
            with self.subTest(old_flag=old_flag):
                e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
                store=e.station('Build',home['sector']);store.update({'class':'buildstorage','base':home})
                e.needs[0].update(owner=store,unitprice=90)
                cfg.update(allowResources=False,allowIntermediates=False,allowTradewares=old_flag)
                ship['defaultorder'].update(cfg)
                self.dispatch(e,hub)
                self.assertEqual(len(ship['orders']),2)
                self.assertIs(ship['orders'][1]['offer']['owner'],store)

    def test_retired_price_modifier_no_longer_blocks_owned_transfers(self):
        for owner in ('player','ai'):
            for modifier,accepted in ((100,False),(85,True),(0,True)):
                with self.subTest(owner=owner,modifier=modifier):
                    e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
                    e.needs[0]['unitprice']=90;e.sources[0]['owner']['owner']=owner;cfg['playerBuyMod']=modifier
                    plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':True})['plan']
                    self.assertEqual(bool(plan),owner=='player')
                    if plan:self.assertEqual(plan[0]['buyprice'],0)

    def test_free_owned_transfer_competes_at_zero_cost_at_equal_distance(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        e.needs[0]['unitprice']=150;cfg['playerBuyMod']=0
        own=e.offer(e.station('Own',home['sector']),e.wares[0],80,110,False)
        hub=e.hub(home,fleet)
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':True})['plan']
        self.assertIs(plan[0]['source'],own);self.assertEqual(plan[0]['buyprice'],0)

    def test_main_source_prefers_fewer_jumps_then_lower_price(self):
        for same_sector in (False,True):
            with self.subTest(same_sector=same_sector):
                e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
                e.needs[0]['unitprice']=150
                remote=e.sector('Remote');e.connect(home['sector'],remote)
                cheaper=e.offer(e.station('Cheap',home['sector'] if same_sector else remote,'ai'),e.wares[0],80,90,False)
                plan=e.runner.script('mule.fleet.plan',{'hub':e.hub(home,fleet),'cfg':cfg,'relaxed':True})['plan']
                self.assertIs(plan[0]['source'],cheaper if same_sector else e.sources[0])

    def test_stock_trigger_allows_whole_shipment_without_retained_reserve(self):
        for stock,accepted in ((59,False),(60,False),(80,True)):
            with self.subTest(stock=stock):
                e,home,fleet,hub=self.setup_market([100],[100]);source=e.sources[0];ware=e.wares[0];ship,cfg=fleet[0]
                source['owner']['owner']='player';source['owner']['cargo'][ware].update(count=stock,target=100)
                cfg['playerStockThreshold']=60;ship['defaultorder']['playerStockThreshold']=60
                source['total']=stock;e.needs[0]['unitprice']=1
                self.dispatch(e,hub)
                self.assertEqual(bool(ship['orders']),accepted)
                if accepted:self.assertEqual(ship['orders'][0]['amount'],80)

    def test_low_owned_stock_falls_back_to_npc_with_priority_on_or_off(self):
        for enabled,stock in ((True,10),(False,10)):
            with self.subTest(enabled=enabled):
                e,home,fleet,hub=self.setup_market([80],[100]);ware=e.wares[0];ship,cfg=fleet[0]
                own=e.offer(e.station('Own',home['sector']),ware,100,200,False)
                own['owner']['cargo'][ware]['count']=stock
                cfg['allowPlayerSuppliers']=enabled;ship['defaultorder']['allowPlayerSuppliers']=enabled
                self.dispatch(e,e.hub(home,fleet))
                self.assertIs(ship['orders'][0]['offer'],e.sources[0])

    def test_owned_priority_overrides_main_distance_in_either_candidate_order(self):
        for enabled in (False,True):
            for owned_first in (False,True):
                with self.subTest(enabled=enabled,owned_first=owned_first):
                    e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
                    remote=e.sector('Remote');e.connect(home['sector'],remote)
                    own=e.offer(e.station('Own',remote),e.wares[0],80,200,False)
                    cfg['allowPlayerSuppliers']=enabled;ship['defaultorder']['allowPlayerSuppliers']=enabled
                    if owned_first:e.sources.reverse()
                    self.dispatch(e,e.hub(home,fleet))
                    self.assertIs(ship['orders'][0]['offer'],own if enabled else next(s for s in e.sources if s is not own))

    def test_priority_off_still_uses_owned_source_without_npc_permission(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        source=e.sources[0];source['owner']['owner']='player';e.needs[0]['unitprice']=1
        cfg.update(allowPlayerSuppliers=False,allowAiSuppliers=False);ship['defaultorder'].update(cfg)
        self.dispatch(e,hub)
        self.assertIs(ship['orders'][0]['offer'],source);self.assertEqual(ship['orders'][0]['amount'],80)

    def test_owned_priority_skips_ineligible_sources_and_falls_back_to_npc(self):
        for reason in ('threshold','equal_threshold','zero_target','empty','blocked','dock','range'):
            with self.subTest(reason=reason):
                e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];ware=e.wares[0]
                remote=e.sector('Remote');e.connect(home['sector'],remote)
                own=e.offer(e.station('Own',remote),ware,80,100,False)
                if reason=='threshold':own['owner']['cargo'][ware]['count']=10
                if reason=='equal_threshold':own['owner']['cargo'][ware]['count']=40
                if reason=='zero_target':own['owner']['cargo'][ware]['target']=0
                if reason=='empty':own['total']=0
                if reason=='blocked':ship['blocked'].add(remote)
                if reason=='dock':own['owner']['docks']=('XL',)
                if reason=='range':cfg['maxDist']=0;ship['defaultorder']['maxDist']=0
                self.dispatch(e,e.hub(home,fleet))
                self.assertIs(ship['orders'][0]['offer'],e.sources[0])

    def test_owned_priority_partial_supply_combines_npc_remainder(self):
        e,home,fleet,hub=self.setup_market([100],[100]);ship,cfg=fleet[0];ware=e.wares[0]
        remote=e.sector('Remote');e.connect(home['sector'],remote)
        own=e.offer(e.station('Own',remote),ware,30,100,False)
        self.dispatch(e,e.hub(home,fleet))
        buys=[o for o in ship['orders'] if not o['offer']['isbuyoffer']]
        self.assertEqual([(o['offer'],o['amount']) for o in buys],[(own,30),(e.sources[0],70)])

    def test_owned_priority_still_ranks_owned_sources_by_distance(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        remote=e.sector('Remote');e.connect(home['sector'],remote)
        e.offer(e.station('FarOwn',remote),e.wares[0],80,100,False)
        near=e.offer(e.station('NearOwn',home['sector']),e.wares[0],80,200,False)
        self.dispatch(e,e.hub(home,fleet))
        self.assertIs(ship['orders'][0]['offer'],near)

    def test_secondary_owned_priority_respects_corridor_and_off_uses_normal_price(self):
        for enabled,inside in ((True,True),(False,True),(True,False)):
            with self.subTest(enabled=enabled,inside=inside):
                e,home,fleet,off,far,p=self.corridor_market();ship,cfg=fleet[0]
                cfg['allowPlayerSuppliers']=enabled;ship['defaultorder']['allowPlayerSuppliers']=enabled
                on=e.offer(e.station('On',p,'ai'),off['ware'],200,90,False)
                off['owner']['owner']='player'
                if not inside:off['owner']['sector']=far['owner']['sector']
                # A priced owned quote makes price and priority genuinely compete.
                off.field=lambda key,field=off.field: Proxy(lambda buyer:False) if key=='buyfree' else field(key)
                self.dispatch(e,e.hub(home,fleet))
                buys=[o['offer'] for o in ship['orders'] if not o['offer']['isbuyoffer']]
                if enabled and not inside:
                    # The owned supplier can become the main pickup. Check every secondary
                    # against that selected main's actual corridor, rather than an old anchor.
                    self.assertIs(buys[0],off)
                    path=e.path(home['sector'],buys[0]['owner']['sector'],ship) or []
                    corridor=[home['sector'],buys[0]['owner']['sector']]+path
                    for pickup in buys[1:]:
                        self.assertLessEqual(min(len(e.path(s,pickup['owner']['sector'],ship))-1 for s in corridor),cfg['corridorDepth'])
                else:
                    self.assertIn(off if enabled else on,buys)
                    self.assertNotIn(on if enabled else off,buys)

    def test_live_priority_toggle_rejects_old_plan_before_commit(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':True})['plan']
        ship['defaultorder']['allowPlayerSuppliers']=False
        self.assertFalse(e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok']);self.assertEqual(e.created,0)

    def test_mimic_forwards_priority_and_stock_threshold_including_zero(self):
        node=ET.parse(SRC/'aiscripts/order.assist.xml').getroot().find('./add/do_if/run_script')
        for enabled,threshold in ((True,60),(False,0),(False,None)):
            with self.subTest(enabled=enabled,threshold=threshold):
                e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
                origin=Obj(ship['defaultorder']);origin['allowPlayerSuppliers']=enabled
                if threshold is None:origin.pop('playerStockThreshold')
                else:origin['playerStockThreshold']=threshold
                captured={}
                e.runner.script=lambda name,params: captured.update(params) or {}
                e.runner.action(node,{'order':origin,'scriptname':'supplymule'})
                self.assertEqual(captured['allowPlayerSuppliers'],enabled)
                self.assertEqual(captured['playerStockThreshold'],50 if threshold is None else threshold)

    def test_priority_upgrade_invalidates_old_configuration_signature(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        fresh=e.runner.script('mule.fleet.config',{'cfg':Obj(cfg)})['cfg']
        old=Obj(fresh);old['signature']=old['signature'].rsplit('/v',1)[0]+'/v15'
        ship['pilot']['mwexFleetCfg']=old
        resolved=e.runner.script('mule.fleet.config',{'cfg':Obj(cfg)})['cfg']
        self.assertIsNot(resolved,old);self.assertTrue(resolved['allowPlayerSuppliers'])
        self.assertEqual(resolved['playerStockThreshold'],50)

    def test_stock_trigger_is_per_ware_and_zero_target_does_not_divide(self):
        e,home,fleet,hub=self.setup_market([80,80],[200]);ship,cfg=fleet[0]
        for source in e.sources:source['owner']['owner']='player'
        e.sources[0]['owner']['cargo'][e.wares[0]]['target']=0
        self.dispatch(e,hub)
        self.assertEqual([o['offer']['ware'] for o in ship['orders'] if not o['offer']['isbuyoffer']],[e.wares[1]])

    def test_live_stock_drop_rejects_unsubmitted_plan(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];source=e.sources[0]
        source['owner']['owner']='player'
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':True})['plan']
        source['owner']['cargo'][e.wares[0]]['count']=10
        self.assertFalse(e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok']);self.assertEqual(e.created,0)

    def test_zero_stock_trigger_survives_config_refresh_and_live_change_rejects(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];source=e.sources[0]
        source['owner']['owner']='player';source['owner']['cargo'][e.wares[0]].update(count=1,target=100)
        source['total']=1;cfg['playerStockThreshold']=0;ship['defaultorder']['playerStockThreshold']=0
        refreshed=e.runner.script('mule.fleet.config',{'cfg':cfg})['cfg']
        self.assertEqual(refreshed['playerStockThreshold'],0)
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':refreshed,'relaxed':True})['plan']
        self.assertTrue(plan);ship['defaultorder']['playerStockThreshold']=75
        self.assertFalse(e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok']);self.assertEqual(e.created,0)

    def test_native_discount_unlocks_previously_equal_raw_quotes_and_actual_budget(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];source=e.sources[0];ware=e.wares[0]
        source.update(unitprice=710,actualprice=558);e.needs[0]['unitprice']=710
        ware.update(minprice=710,maxprice=1318,pricerange=608)
        e.modifier_targets.add(source['owner']['owner']);ship['money']=558*80
        self.dispatch(e,hub)
        self.assertEqual(len(ship['orders']),2);self.assertEqual(ship['orders'][0]['amount'],80)
        self.assertAlmostEqual(ship['pilot']['mwexFleetVoyage'][0]['buyprice'],558,places=2)

    def test_main_same_distance_ranks_discounted_actual_price(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];source=e.sources[0]
        source.update(unitprice=140,actualprice=90);e.needs[0]['unitprice']=150
        e.modifier_targets.add(source['owner'])
        e.offer(e.station('Competitor',home['sector'],'ai'),e.wares[0],80,100,False)
        self.dispatch(e,e.hub(home,fleet));self.assertIs(ship['orders'][0]['offer'],source)

    def test_secondary_discount_does_not_outweigh_better_route_throughput(self):
        e,home,fleet,off,far,p=self.corridor_market();fleet[0][1]['corridorMarkup']=5
        off.update(unitprice=120,actualprice=90);e.modifier_targets.add(off['owner'])
        on=e.offer(e.station('OnPath',p,'ai'),off['ware'],200,100,False)
        self.dispatch(e,e.hub(home,fleet))
        self.assertIn(on,[o['offer'] for o in fleet[0][0]['orders']])
        self.assertNotIn(off,[o['offer'] for o in fleet[0][0]['orders']])
        self.assertGreater(sum(e.queries.values()),0)

    def test_price_cache_is_shared_by_same_faction_and_scoped_to_supplied_cache(self):
        e,home,fleet,hub=self.setup_market([80],[100,100]);source=e.sources[0]
        source['actualprice']=90;e.modifier_targets.add(source['owner']);cache=Obj()
        first=e.runner.script('mule.fleet.price',{'offer':source,'ship':fleet[0][0],'cache':cache})
        queries=sum(e.queries.values())
        second=e.runner.script('mule.fleet.price',{'offer':source,'ship':fleet[1][0],'cache':cache})
        self.assertEqual(first,second);self.assertEqual(sum(e.queries.values()),queries)
        self.assertLessEqual(queries,33);self.assertEqual(e.created,0)

    def test_commit_reprices_discount_expiry_and_rechecks_spread_and_budget(self):
        for case in ('affordable','budget','spread'):
            with self.subTest(case=case):
                e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];source=e.sources[0]
                source['actualprice']=90;e.modifier_targets.add(source['owner'])
                plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':True})['plan']
                self.assertTrue(plan);source['actualprice']=100;e.modifier_targets.clear()
                if case=='budget':ship['money']=90*80
                if case=='spread':source['unitprice']=110
                ok=e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok']
                self.assertEqual(ok,case=='affordable')
                self.assertEqual(e.created,2 if ok else 0)
                if ok:self.assertEqual(plan[0]['buyprice'],100)

    def test_receiver_commission_is_used_in_price_spread(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        home['owner']='ai';e.needs[0].update(unitprice=90,actualprice=110);e.modifier_targets.add(home)
        self.dispatch(e,hub);self.assertEqual(len(ship['orders']),2)
        self.assertAlmostEqual(ship['pilot']['mwexFleetVoyage'][0]['sellprice'],110,places=2)

    def test_busy_trade_cargo_is_not_liquidated(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];ware=e.wares[0]
        ship['cargo'].update(count=15,list=[ware]);ship['cargo'][ware]['count']=15
        order=e.order(ship,e.needs[0],15);balance=e.player['money'];self.dispatch(e,hub)
        self.assertEqual(ship['cargo'][ware]['count'],15);self.assertEqual(e.player['money'],balance)
        self.assertEqual(ship['orders'],[order])

    def test_price_rejection_explains_prices_in_chinese(self):
        e,home,fleet,hub=self.setup_market([80],[100]);e.needs[0]['unitprice']=90
        self.dispatch(e,hub)
        message=hub['failures'][e.needs[0]]
        self.assertIn('实际采购单价100',message);self.assertIn('实际买报价90',message)
        self.assertNotIn('raw_price',message)

    def test_english_resources_cover_script_references_and_match_format_arguments(self):
        en={n.get('id'):n.text or '' for n in ET.parse(SRC/'t/0001.xml').getroot().iter('t')}
        zh={n.get('id'):n.text or '' for n in ET.parse(SRC/'t/0001-l086.xml').getroot().iter('t')}
        references=set()
        for folder in ('aiscripts','md'):
            for path in (SRC/folder).glob('*.xml'):
                references.update(re.findall(r'\{61552,(\d+)\}',path.read_text(encoding='utf-8')))
        for reference in references:
            with self.subTest(reference=reference):
                self.assertIn(reference,en);self.assertIn(reference,zh)
                self.assertNotRegex(en[reference],r'[\u4e00-\u9fff]')
                self.assertEqual(en[reference].count('%s'),zh[reference].count('%s'))

    def test_english_price_failure_is_attached_to_ship_in_english(self):
        e,home,fleet,hub=self.setup_market([80],[100],language='en');ship,cfg=fleet[0]
        e.needs[0]['unitprice']=90;self.dispatch(e,hub)
        message=cfg['origin']['failure']
        self.assertIn('Price spread rejected',message);self.assertIn('actual buy=100',message)
        self.assertNotRegex(message,r'[\u4e00-\u9fff]')

    def test_english_assignment_warning_is_localized_and_trade_continues(self):
        e,home,fleet,hub=self.setup_market([80],[100],language='en');ship,cfg=fleet[0]
        ship.update(commander=None,reject_assignment=True);ship['pilot'].pop('mwexFleetCfg')
        self.entry(e,ship,cfg,home)
        warnings=self.assignment_notices(e)
        self.assertEqual(len(warnings),1);self.assertIn('could not be assigned',warnings[0])
        self.assertNotRegex(warnings[0],r'[\u4e00-\u9fff]');self.assertEqual(len(ship['orders']),2)

    def test_english_sector_home_warning_is_localized_and_trade_continues(self):
        e,home,fleet,hub=self.setup_market([80],[100],language='en');ship,cfg=fleet[0]
        ship['commander']=None;ship['pilot'].pop('mwexFleetCfg')
        self.entry(e,ship,cfg,home['sector'])
        warnings=self.assignment_notices(e)
        self.assertEqual(len(warnings),1);self.assertIn('has a sector as home',warnings[0])
        self.assertNotRegex(warnings[0],r'[\u4e00-\u9fff]');self.assertEqual(len(ship['orders']),2)

    def test_hidden_tuning_parameters_and_new_stock_slider(self):
        root=ET.parse(SRC/'aiscripts/supply_mule.xml').getroot();params={p.get('name'):p for p in root.findall('./order/params/param')}
        self.assertEqual(params['searchDepth'].get('type'),'internal');self.assertEqual(params['playerBuyMod'].get('type'),'internal')
        self.assertEqual(params['playerStockThreshold'].get('type'),'number')
        for old in (5,40,100):
            e,home,fleet,hub=self.setup_market([100],[100]);fleet[0][1]['searchDepth']=old
            ware=e.wares[0];e.sources.clear()
            for i in range(40):e.offer(e.station('S'+str(i),home['sector'],'ai'),ware,100,100,False)
            self.dispatch(e,e.hub(home,fleet));self.assertLessEqual(e.waits,2)

    def test_old_signature_cannot_reuse_config_without_new_stock_threshold(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        fresh=e.runner.script('mule.fleet.config',{'cfg':Obj(cfg)})['cfg']
        old=Obj(fresh);old.pop('playerStockThreshold');old['signature']=old['signature'].rsplit('/v',1)[0]
        ship['pilot']['mwexFleetCfg']=old
        incoming=Obj(cfg);incoming.pop('playerStockThreshold')
        resolved=e.runner.script('mule.fleet.config',{'cfg':incoming})['cfg']
        self.assertIsNot(resolved,old);self.assertEqual(resolved['playerStockThreshold'],50)

    def test_sector_service_dispatches_to_six_player_factories_and_excludes_npc(self):
        e,home,fleet,hub=self.setup_market([10],[100]);ware=e.wares[0];e.sources[0]['total']=1000
        receivers=[home]
        for i in range(5):
            station=e.station('Player'+str(i),home['sector']);receivers.append(station)
            e.offer(station,ware,10,110,True)
        npc=e.station('NPC',home['sector'],'ai');e.offer(npc,ware,1000,110,True)
        fleet.extend(e.ship('extra'+str(i),100,home) for i in range(5))
        for ship,cfg in fleet:
            cfg.update(home=home['sector'],bound=False);ship['defaultorder']['home']=home['sector']
        self.dispatch(e,e.hub(home['sector'],fleet))
        delivered=[o['offer']['owner'] for ship,cfg in fleet for o in ship['orders'] if o['offer']['isbuyoffer']]
        self.assertCountEqual(delivered,receivers);self.assertNotIn(npc,delivered)

    def test_each_idle_ship_can_dispatch_without_capacity_turn(self):
        e,home,fleet,hub=self.setup_market([150000],[90000,120000,5000])
        fleet[0][0]['size']='XL';fleet[1][0]['size']='L'
        self.dispatch(e,hub)
        self.assertEqual(fleet[1][0]['orders'][0]['amount'],55000)
        self.assertEqual(fleet[2][0]['orders'][0]['amount'],5000)
        self.assertEqual(fleet[0][0]['orders'][0]['amount'],90000)
    def test_three_lots_collect_before_delivery(self):
        e,home,fleet,hub=self.setup_market([350,250,150],[1000,100])
        self.dispatch(e,hub);orders=fleet[0][0]['orders']
        self.assertEqual([o['offer']['isbuyoffer'] for o in orders],[False]*3+[True]*3)
        self.assertCountEqual([o['amount'] for o in orders[:3]],[350,250,150])
    def test_exact_70_percent_is_normal(self):
        e,_,fleet,hub=self.setup_market([350,250,100],[1000,100])
        self.dispatch(e,hub)
        text='\n'.join(x['text'] for x in e.logbook if x['object'] is fleet[0][0])
        self.assertIn('装载70.0%',text);self.assertNotIn('临时解除',text)
        self.assertEqual(sum(o['amount'] for o in fleet[0][0]['orders'][:3]),700)
    def test_below_threshold_reaches_small_ship_first(self):
        e,_,fleet,hub=self.setup_market([699],[1000,100])
        self.dispatch(e,hub)
        self.assertEqual(fleet[1][0]['orders'][0]['amount'],100)
        self.assertEqual(fleet[0][0]['orders'][0]['amount'],599)
    def test_tail_escalates_without_waiting_for_small_delivery(self):
        e,_,fleet,hub=self.setup_market([1]*10,[5000,90000,120000])
        self.dispatch(e,hub)
        self.assertEqual([len(s['orders']) for s,c in fleet],[6,6,6])
        self.assertEqual(sum(n.field('amount') for n in e.needs),1)
    def test_ten_tail_lots_use_four_of_ten_ships(self):
        e,_,fleet,hub=self.setup_market([1]*10,[5000]*10)
        self.dispatch(e,hub)
        self.assertEqual(sorted(len(s['orders'])//2 for s,c in fleet if s['orders']),[1,3,3,3])
    def test_busy_small_ship_does_not_block_medium(self):
        e,_,fleet,hub=self.setup_market([1]*4,[5000,90000])
        fleet[0][0]['orders'].append(Obj(id='Wait'))
        self.dispatch(e,hub)
        self.assertEqual(len(fleet[1][0]['orders']),6)
    def test_N_is_never_relaxed(self):
        e,_,fleet,hub=self.setup_market([300,200,150,100],[1000])
        self.dispatch(e,hub)
        self.assertLessEqual(len(fleet[0][0]['orders']),6)
        self.assertEqual(sum(o['amount'] for o in fleet[0][0]['orders'] if not o['offer']['isbuyoffer']),500)
    def test_cumulative_budget(self):
        e,_,fleet,hub=self.setup_market([6,6,6],[1000])
        ship,cfg=fleet[0];ship['money']=1000
        self.dispatch(e,hub)
        self.assertEqual(sum(o['amount']*100 for o in ship['orders'] if not o['offer']['isbuyoffer']),1000)
    def test_delayed_native_visibility_does_not_duplicate_last_unit(self):
        e,_,fleet,hub=self.setup_market([1],[100]*12,delayed=True)
        self.dispatch(e,hub)
        self.assertEqual(sum(bool(s['orders']) for s,c in fleet),1)
    def test_native_handoff_avoids_double_subtraction(self):
        e,_,fleet,hub=self.setup_market([1000],[100],delayed=True)
        self.dispatch(e,hub);need=e.needs[0];ship=fleet[0][0]
        self.assertEqual(e.runner.script('mule.fleet.net',{'offer':need})['net'],900)
        need.reserved[ship]=100
        self.assertEqual(e.runner.script('mule.fleet.net',{'offer':need})['net'],900)
    def test_cancel_releases_hold_once(self):
        e,_,fleet,hub=self.setup_market([1000],[100],delayed=True)
        self.dispatch(e,hub)
        for order in list(fleet[0][0]['orders']):e.cancel(order)
        self.assertEqual(e.runner.script('mule.fleet.net',{'offer':e.needs[0]})['net'],1000)
    def test_submission_failure_cancels_only_created_orders(self):
        e,_,fleet,hub=self.setup_market([350,250,150],[1000])
        e.fail_at=4;self.dispatch(e,hub)
        self.assertEqual(fleet[0][0]['orders'],[])
        self.assertTrue(e.notices)
    def test_blacklisted_supplier_is_not_used(self):
        e,_,fleet,hub=self.setup_market([100],[100])
        fleet[0][0]['forbidden'].add(e.sources[0]['owner'])
        self.dispatch(e,hub)
        self.assertEqual(fleet[0][0]['orders'],[])
    def test_incompatible_dock_is_not_used(self):
        e,_,fleet,hub=self.setup_market([100],[100])
        e.sources[0]['owner']['docks']=('S',)
        self.dispatch(e,hub)
        self.assertEqual(fleet[0][0]['orders'],[])
    def test_idle_cargo_liquidates_once_at_average_price(self):
        e,_,fleet,hub=self.setup_market([0],[100]);ship=fleet[0][0];w=e.wares[0]
        ship['cargo']['count']=1;ship['cargo']['list']=[w];ship['cargo'][w]['count']=15
        balance=e.player['money'];self.dispatch(e,hub)
        self.assertEqual(ship['cargo'][w]['count'],0)
        self.assertEqual(e.player['money']-balance,15*w['averageprice'])
        self.assertEqual(ship['orders'],[])
        self.dispatch(e,hub);self.assertEqual(e.player['money']-balance,15*w['averageprice'])
    def test_no_demand_creates_no_orders(self):
        e,_,fleet,hub=self.setup_market([0],[100]*200)
        self.dispatch(e,hub)
        self.assertEqual(e.created,0)
    def test_200_s_share_one_tail_unit(self):
        e,_,fleet,hub=self.setup_market([1],[100]*200)
        self.dispatch(e,hub)
        self.assertEqual(e.created,2)
    def test_35_mixed_all_used_for_adequate_supply(self):
        e,_,fleet,hub=self.setup_market([10**7],[100000]*5+[20000]*10+[4000]*20)
        self.dispatch(e,hub)
        self.assertEqual(sum(bool(s['orders']) for s,c in fleet),35)
    def test_supplier_checks_page_without_discarding_late_source(self):
        e,home,fleet,hub=self.setup_market([100],[100]);w=e.wares[0]
        e.sources.clear()
        for i in range(40):
            s=e.station('s'+str(i),home['sector'],'ai');offer=e.offer(s,w,100,120 if i<39 else 100,False)
        hub=e.hub(home,fleet);self.dispatch(e,hub)
        self.assertEqual(fleet[0][0]['orders'][0]['offer']['owner']['name'],'s39')
        self.assertLess(e.action_calls['MWEX_Fleet_candidate'],40)
    def corridor_market(self):
        e=Engine();h=e.sector('H');p=e.sector('P');a=e.sector('A');b=e.sector('B');c=e.sector('C')
        e.connect(h,p);e.connect(p,a);e.connect(p,b);e.connect(b,c)
        home=e.station('Home',h);wa=e.ware('A');wb=e.ware('B');wc=e.ware('C')
        e.offer(home,wa,600,200,True);e.offer(home,wb,200,103,True);e.offer(home,wc,100,200,True)
        e.offer(e.station('main',a,'ai'),wa,600,100,False)
        off=e.offer(e.station('off',b,'ai'),wb,200,100,False)
        far=e.offer(e.station('third',c,'ai'),wc,100,100,False)
        fleet=[e.ship('ship',1000,home,corridorDepth=1)]
        return e,home,fleet,off,far,p
    def test_single_secondary_survives_weighted_price_above_receiver_price(self):
        e,home,fleet,off,far,p=self.corridor_market();hub=e.hub(home,fleet)
        self.dispatch(e,hub)
        buys=[o['offer'] for o in fleet[0][0]['orders'] if not o['offer']['isbuyoffer']]
        self.assertIn(off,buys);self.assertNotIn(far,buys)
    def test_secondary_price_competition_prefers_103_on_path_to_100_off(self):
        e,home,fleet,off,far,p=self.corridor_market()
        # Raise receiver quote to retain the existing strict RAW positive-profit rule.
        e.needs[1]['unitprice']=110
        on=e.offer(e.station('on',p,'ai'),off['ware'],200,103,False)
        self.dispatch(e,e.hub(home,fleet))
        buys=[o['offer'] for o in fleet[0][0]['orders'] if not o['offer']['isbuyoffer']]
        self.assertIn(on,buys);self.assertNotIn(off,buys)
    def test_zero_markup_keeps_depth_restriction(self):
        e,home,fleet,off,far,p=self.corridor_market();fleet[0][1]['corridorMarkup']=0
        self.dispatch(e,e.hub(home,fleet))
        self.assertNotIn(far,[o['offer'] for o in fleet[0][0]['orders']])
    def test_home_range_remains_hard_limit(self):
        e,home,fleet,off,far,p=self.corridor_market()
        # Main fits; third is only two jumps from corridor but three from home.
        fleet[0][1]['maxDist']=2;fleet[0][0]['defaultorder']['maxDist']=2;fleet[0][1]['corridorDepth']=2
        self.dispatch(e,e.hub(home,fleet))
        self.assertNotIn(far,[o['offer'] for o in fleet[0][0]['orders']])

    def run_roster(self,e,home,fleet,hub):
        e.global_['mwexFleetHubs']=Obj({home:hub});e.global_.pop('mwexFleetLock',None)
        hub['rosterDirty']=True;hub['next']=e.player['age'];hub['rosterUntil']=0
        e.roots['this']=Obj(ship=fleet[0][0],name='pilot')
        e.runner.script('mule.fleet.run',{'cfg':fleet[0][1]})
    def test_roster_same_model_count_change_keeps_members_without_sorting(self):
        e,home,fleet,hub=self.setup_market([0],[100,200])
        self.run_roster(e,home,fleet,hub);sorts=sum(x.startswith('SORT') for x in e.logs)
        new=e.ship('new',100,home,macro='model100');fleet.append(new)
        self.run_roster(e,home,fleet,hub)
        self.assertEqual(len(hub['members']),3)
        self.assertEqual(sum(x.startswith('SORT') for x in e.logs),sorts)
        self.assertIn(new[0],hub['members'])
        self.assertNotIn('groups',hub)
    def test_roster_new_model_and_destroyed_ship_update_members_without_capacity_queue(self):
        e,home,fleet,hub=self.setup_market([0],[100,200]);self.run_roster(e,home,fleet,hub)
        new=e.ship('new',150,home,macro='newmodel');fleet.append(new)
        self.run_roster(e,home,fleet,hub)
        self.assertCountEqual(hub['members'],[s for s,c in fleet])
        new[0]['isoperational']=False;self.run_roster(e,home,fleet,hub)
        self.assertNotIn(new[0],hub['members'])
        self.assertCountEqual(hub['members'],[fleet[0][0],fleet[1][0]])
        self.assertNotIn('ascending',hub)
    def test_roster_detached_ship_not_readded(self):
        e,home,fleet,hub=self.setup_market([0],[100,200]);self.run_roster(e,home,fleet,hub)
        fleet[1][0]['commander']=Obj(name='other');self.run_roster(e,home,fleet,hub)
        self.assertNotIn(fleet[1][0],hub['members'])
    def test_roster_refresh_preserves_each_ships_stock_threshold_including_zero(self):
        for threshold in (0,60,100):
            with self.subTest(threshold=threshold):
                e,home,fleet,hub=self.setup_market([0],[100,200])
                candidate,old_cfg=fleet[1]
                candidate['defaultorder']['playerStockThreshold']=threshold
                self.run_roster(e,home,fleet,hub)
                self.assertEqual(hub['members'][candidate]['playerStockThreshold'],threshold)
                self.assertEqual(candidate['pilot']['mwexFleetCfg']['playerStockThreshold'],threshold)
    def test_discovery_indexes_sources_by_ware_sector_owner(self):
        e,home,fleet,hub=self.setup_market([100,0],[100])
        e.runner.script('mule.fleet.discover',{'hub':hub})
        self.assertEqual(hub['sources'][e.wares[0]][home['sector']][e.sources[0]['owner']],e.sources[0])
        self.assertNotIn(e.wares[1],hub['sources'])
        self.assertEqual(e.queries['find_sell_offer'],1)
    def test_no_demand_discovery_skips_supplier_query(self):
        e,home,fleet,hub=self.setup_market([0],[100]*200)
        e.runner.script('mule.fleet.discover',{'hub':hub})
        self.assertNotIn('find_sell_offer',e.queries)
    def test_two_hubs_share_delayed_supplier_holds(self):
        e,home,fleet,hub=self.setup_market([80],[100],delayed=True)
        e.sources[0]['total']=100
        home2=e.station('Home2',home['sector']);e.offer(home2,e.wares[0],80,110,True)
        fleet2=[e.ship('second',100,home2)];hub2=e.hub(home2,fleet2)
        self.dispatch(e,hub);self.dispatch(e,hub2)
        self.assertEqual(fleet[0][0]['orders'][0]['amount'],80)
        self.assertEqual(fleet2[0][0]['orders'][0]['amount'],20)
    def test_partial_running_order_keeps_hold_for_actual_amount(self):
        e,home,fleet,hub=self.setup_market([100],[100],delayed=True)
        e.partial_at=1;e.running_partial=True;self.dispatch(e,hub)
        self.assertEqual(len(fleet[0][0]['orders']),1)
        self.assertEqual(e.runner.script('mule.fleet.net',{'offer':e.sources[0]})['net'],50)
        self.assertIn('mwexFleetVoyage',fleet[0][0]['pilot'])
    def test_liquidation_allows_new_procurement_in_same_wave(self):
        e,home,fleet,hub=self.setup_market([100],[100]);ship,cfg=fleet[0];ware=e.wares[0]
        ship['cargo'].update(count=1,list=[ware]);ship['cargo'][ware]['count']=15
        balance=e.player['money'];self.dispatch(e,hub)
        self.assertEqual(len(ship['orders']),2);self.assertFalse(ship['orders'][0]['offer']['isbuyoffer'])
        self.assertEqual(ship['cargo'][ware]['count'],0)
        self.assertEqual(e.player['money']-balance,15*ware['averageprice'])
    def test_d0_requires_route_sector(self):
        e,home,fleet,off,far,p=self.corridor_market()
        fleet[0][1]['corridorDepth']=0;fleet[0][0]['defaultorder']['corridorDepth']=0
        self.dispatch(e,e.hub(home,fleet))
        self.assertNotIn(off,[o['offer'] for o in fleet[0][0]['orders']])
    def test_live_parameter_change_rejects_plan_without_orders(self):
        e,home,fleet,hub=self.setup_market([100],[100]);ship,cfg=fleet[0]
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':False})['plan']
        ship['defaultorder']['allowAiSuppliers']=False
        self.assertFalse(e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok'])
        self.assertEqual(e.created,0)
    def test_late_blacklist_change_rejects_draft_before_commit(self):
        e,home,fleet,hub=self.setup_market([100],[100]);ship,cfg=fleet[0]
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':False})['plan']
        ship['forbidden'].add(e.sources[0]['owner'])
        self.assertFalse(e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok'])
        self.assertEqual(e.created,0)
    def test_detached_after_draft_rejects_before_commit(self):
        e,home,fleet,hub=self.setup_market([100],[100]);ship,cfg=fleet[0]
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':False})['plan']
        ship['commander']=Obj(name='other')
        self.assertFalse(e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok'])
        self.assertEqual(e.created,0)
    def test_discovery_event_inserts_source_without_rebuilding_index(self):
        e,home,fleet,hub=self.setup_market([100],[100]);hub['range']=10
        e.global_['mwexFleetHubs']=Obj({home:hub})
        new=e.offer(e.station('new',home['sector'],'ai'),e.wares[0],100,99,False)
        e.roots['event']=Obj(param=new)
        md=ET.parse(SRC/'md/mwex_capacity.xml').getroot()
        actions=md.find('./cues/cue[@name="Discovered"]/actions')
        e.runner.children(list(actions),{})
        self.assertIs(hub['sources'][e.wares[0]][home['sector']][new['owner']],new)
        self.assertEqual(e.queries,{})
    def test_active_entry_has_no_unreachable_legacy_trade_loop(self):
        root=ET.parse(SRC/'aiscripts/supply_mule.xml').getroot()
        actions=list(root.find('./attention/actions'))
        dispatch=next(i for i,n in enumerate(actions) if n.tag=='run_script' and n.get('name')=="'mule.fleet.run'")
        self.assertEqual(actions[dispatch+1].tag,'return')
        self.assertEqual(actions[dispatch+2:],[])
    def test_receiver_storage_is_shared_across_draft_lots(self):
        e,home,fleet,hub=self.setup_market([80,80],[1000]);home['cargo']['free']['container']=100
        self.dispatch(e,hub)
        self.assertEqual(sum(o['amount'] for o in fleet[0][0]['orders'] if o['offer']['isbuyoffer']),80)
    def test_foreign_receiver_budget_is_shared_across_lots(self):
        e,home,fleet,hub=self.setup_market([6,6,6],[1000]);home['owner']='ai';home['money']=1100
        self.dispatch(e,hub)
        self.assertEqual(sum(o['amount'] for o in fleet[0][0]['orders'] if o['offer']['isbuyoffer']),10)
    def test_mimic_registered_config_uses_capacity_dispatch(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        ship['defaultorder']=Obj(id='Assist');cfg['origin']=ship['defaultorder']
        self.dispatch(e,hub)
        self.assertEqual(len(ship['orders']),2)
    def test_sector_home_uses_sector_scope_and_native_limits(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        sector=home['sector'];cfg.update(home=sector,bound=False);ship['defaultorder']['home']=sector
        hub['home']=sector;self.dispatch(e,hub)
        self.assertEqual(len(ship['orders']),2)
    def test_load_preserves_pending_holds_and_invalidates_only_caches(self):
        e,home,fleet,hub=self.setup_market([100],[100],delayed=True);self.dispatch(e,hub)
        e.global_['mwexFleetHubs']=Obj({home:hub});pending=e.global_['mwexFleetPending'][:]
        md=ET.parse(SRC/'md/mwex_capacity.xml').getroot()
        e.runner.children(list(md.find('./cues/cue[@name="Loaded"]/actions')), {})
        self.assertNotIn('mwexFleetLock',e.global_)
        self.assertEqual(e.global_['mwexFleetPending'],pending)
        self.assertTrue(hub['rosterDirty']);self.assertTrue(hub['cacheDirty'])
        self.assertEqual(len(fleet[0][0]['orders']),2)
    def test_receiver_shared_storage_change_rejects_before_commit(self):
        e,home,fleet,hub=self.setup_market([80,80],[200]);ship,cfg=fleet[0]
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':False})['plan']
        home['cargo']['free']['container']=100
        self.assertFalse(e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})['ok'])
        self.assertEqual(e.created,0)

    def return_home(self,e,hub,ship,cfg):
        e.global_.pop('mwexFleetLock',None)
        e.roots['this']=Obj(ship=ship,assignedcontrolled=ship)
        e.runner.script('mule.fleet.home',{'cfg':cfg,'hub':hub})

    def test_idle_ship_returns_while_other_member_trades_has_manual_orders_or_is_player_flown(self):
        for home_kind in ('station','sector'):
            for busy_kind in ('trade','manual','player'):
                with self.subTest(home=home_kind,other_member=busy_kind):
                    e,home,fleet,hub=self.setup_market([80 if busy_kind=='trade' else 0],[100,200])
                    ship,cfg=fleet[0];other,other_cfg=fleet[1]
                    if busy_kind=='trade':
                        e.order(other,e.sources[0],80);e.order(other,e.needs[0],80)
                    if busy_kind=='manual':other['orders'].append(Obj(id='MoveWait'))
                    if busy_kind=='player':e.player['occupiedship']=other
                    other_orders=list(other['orders'])
                    target=home if home_kind=='station' else home['sector']
                    if home_kind=='sector':
                        hub['home']=target
                        for member,member_cfg in fleet:
                            member_cfg['home']=target;member_cfg['origin']['home']=target
                    far=e.sector('far');e.connect(home['sector'],far)
                    ship.update(sector=far,zone=Obj(name='far zone'))
                    cfg['playerStockThreshold']=cfg['origin']['playerStockThreshold']=50.0
                    e.global_['mwexFleetHubs']=Obj({target:hub});e.global_.pop('mwexFleetLock',None)
                    e.roots['this']=Obj(ship=ship,assignedcontrolled=ship)
                    e.runner.script('mule.fleet.run',{'cfg':cfg})
                    self.assertEqual(len(e.moves),1)
                    self.assertIs(e.moves[0]['destination'],target if home_kind=='station' else target['returnzone'])
                    self.assertEqual(ship['orders'],[]);self.assertEqual(other['orders'],other_orders)
                    self.assertIs(ship['sector'],home['sector'])

    def test_station_home_returns_to_station_zone_without_occupying_a_berth(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        far=e.sector('far');e.connect(home['sector'],far)
        ship.update(sector=far,zone=Obj(name='far zone'));self.dispatch(e,hub)
        self.return_home(e,hub,ship,cfg)
        self.assertIs(ship['zone'],home['zone']);self.assertEqual(ship['orders'],[])
        self.assertIs(e.moves[0]['destination'],home)
        self.assertTrue(e.moves[0]['endintargetzone']);self.assertTrue(e.moves[0]['strictblacklist'])
        self.return_home(e,hub,ship,cfg);self.assertEqual(len(e.moves),1)

    def test_sector_home_stops_inside_sector_and_does_not_force_station_visit(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        cfg['home']=home['sector'];cfg['origin']['home']=home['sector'];hub['home']=home['sector']
        ship['zone']=Obj(name='other zone in home sector')
        self.return_home(e,hub,ship,cfg);self.assertEqual(e.moves,[])
        far=e.sector('far');e.connect(home['sector'],far);ship['sector']=far
        self.return_home(e,hub,ship,cfg)
        self.assertIs(e.moves[0]['destination'],home['sector']['returnzone'])
        self.assertTrue(e.moves[0]['endintargetsector']);self.assertTrue(e.moves[0]['endintargetzone'])

    def test_idle_return_never_takes_player_busy_detached_or_reconfigured_ship(self):
        for case in ('trade','manual','occupied','detached','default','cfg','dead','home-gone'):
            with self.subTest(case=case):
                e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
                ship['zone']=Obj(name='remote zone')
                if case=='trade':ship['orders'].append(Obj(id='Trade'))
                if case=='manual':ship['orders'].append(Obj(id='Wait'))
                if case=='occupied':e.player['occupiedship']=ship
                if case=='detached':ship['commander']=None
                if case=='default':ship['defaultorder']=Obj(id='Wait')
                if case=='cfg':ship['pilot']['mwexFleetCfg']=Obj()
                if case=='dead':ship['isoperational']=False
                if case=='home-gone':home['exists']=False
                self.return_home(e,hub,ship,cfg);self.assertEqual(e.moves,[])

    def test_blacklist_prevents_return_and_reason_is_attached_to_this_ship(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        far=e.sector('far');e.connect(home['sector'],far)
        ship.update(sector=far,zone=Obj(name='far zone'));ship['blocked'].add(home['sector'])
        self.return_home(e,hub,ship,cfg)
        self.assertEqual(e.moves,[]);self.assertIn('旅行黑名单',cfg['origin']['failure'])
        self.assertIn(ship['idcode'],cfg['origin']['failure'])

    def test_return_is_outside_lock_and_new_demand_can_dispatch_before_arrival(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        # The adapter distinguishes Python 50 from 50.0 in string formatting; X4 numeric values do not.
        cfg['playerStockThreshold']=cfg['origin']['playerStockThreshold']=50.0
        far=e.sector('far');e.connect(home['sector'],far);ship.update(sector=far,zone=Obj(name='far zone'))
        def unfinished_move(engine,params):
            self.assertNotIn('mwexFleetLock',engine.global_)
            engine.player['age']+=30
            engine.needs[0]['total']=80
            engine.sources[0]['total']=80
        e.on_move=unfinished_move
        e.roots['this']=Obj(ship=ship,assignedcontrolled=ship)
        e.global_.pop('mwexFleetLock',None)
        e.runner.script('mule.fleet.run',{'cfg':cfg})
        self.assertEqual(len(e.moves),1);self.assertIs(ship['sector'],far)
        e.runner.script('mule.fleet.run',{'cfg':cfg})
        self.assertEqual(len(ship['orders']),2);self.assertNotIn('failure',cfg['origin'])
        self.assertEqual(len(e.moves),1)

    def test_return_poll_keeps_flight_and_stale_timers_cannot_interrupt_new_leg(self):
        root=ET.parse(SRC/'aiscripts/mule.fleet.home.xml').getroot()
        timer=root.find('./attention/actions/signal_objects');self.assertEqual(timer.get('delay'),'30s')
        conditions=root.find('./interrupts/handler/conditions')
        self.assertEqual(conditions.find('event_object_signalled').get('param'),timer.get('param'))
        actions=root.find('./interrupts/handler/actions')
        self.assertIsNone(actions.find('abort_called_scripts'))
        self.assertEqual(actions.find('run_interrupt_script').get('abortscripts'),'false')
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        ship['zone']=Obj(name='remote zone')
        self.return_home(e,hub,ship,cfg)
        old=e.signals[-1]['param2'];ship['zone']=Obj(name='remote zone again')
        self.return_home(e,hub,ship,cfg);new=e.signals[-1]['param2']
        self.assertNotEqual(old,new);self.assertEqual(e.signals[-1]['delay'],30)
        expr=conditions.find('check_value').get('value')
        e.roots['event']=Obj(param2=old)
        self.assertFalse(e.runner.value(expr,{'returnToken':new}))
        e.roots['event']=Obj(param2=new)
        self.assertTrue(e.runner.value(expr,{'returnToken':new}))

    def test_sector_return_avoids_native_null_sector_shortcut(self):
        for zone in (True,False):
            with self.subTest(has_normal_zone=zone):
                e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
                sector=home['sector'];sector['sector']=None
                cfg['home']=cfg['origin']['home']=hub['home']=sector;cfg['bound']=False
                far=e.sector('far');e.connect(far,sector);ship['sector']=far
                if not zone:sector['returnzone']=None
                self.return_home(e,hub,ship,cfg)
                move=e.moves[0];destination=move['destination']
                self.assertFalse(move['endintargetsector'] and not destination.get('sector'))
                self.assertFalse(move['noboost'])
                if zone:self.assertIs(destination['sector'],sector)

    def test_periodic_poll_without_trade_preserves_current_movement(self):
        e,home,fleet,hub=self.setup_market([0],[100]);ship,cfg=fleet[0]
        cfg=e.runner.script('mule.fleet.config',{'cfg':cfg})['cfg'];hub['members'][ship]=cfg
        e.global_['mwexFleetHubs']=Obj({home:hub});hub['next']=0
        ship['zone']=Obj(name='far zone')
        root=ET.parse(SRC/'aiscripts/mule.fleet.home.xml').getroot()
        actions=root.find('./interrupts/handler/actions')
        def poll(engine,params):
            for _ in range(3):
                engine.player['age']+=30
                local=dict(cfg=cfg,hub=hub,ship=ship,home=home,returnToken=ship['pilot']['mwexFleetReturnSerial'])
                engine.runner.children(list(actions),local)
        e.on_move=poll
        self.return_home(e,hub,ship,cfg)
        self.assertEqual(len(e.moves),1)
        self.assertEqual(ship['orders'],[])
        self.assertEqual(len([s for s in e.signals if s['param']=='mwex_capacity_return_refresh']),4)
        self.assertFalse(any(s['param']=='mwex_capacity_return_stop' for s in e.signals))

    def test_periodic_poll_assigns_new_trade_and_signals_only_real_departure(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        cfg=e.runner.script('mule.fleet.config',{'cfg':cfg})['cfg'];hub['members'][ship]=cfg
        e.global_['mwexFleetHubs']=Obj({home:hub});e.global_.pop('mwexFleetLock',None)
        e.roots['this']=Obj(ship=ship,assignedcontrolled=ship)
        ship['pilot']['mwexFleetReturnSerial']=7
        e.runner.script('mule.fleet.run',{'cfg':cfg,'once':True,'returnToken':7})
        self.assertEqual(len(ship['orders']),2);self.assertEqual(e.moves,[])
        stop=[s for s in e.signals if s['param']=='mwex_capacity_return_stop']
        self.assertEqual(len(stop),1);self.assertEqual(stop[0]['param2'],7)
        self.assertNotIn('mwexFleetLock',e.global_)

    def test_semantically_identical_saved_config_copy_can_plan_and_commit(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        copied=Obj(cfg);self.assertIsNot(copied,cfg)
        hub['members'][ship]=copied
        self.dispatch(e,hub)
        self.assertEqual(len(ship['orders']),2)

    def test_unrestricted_sector_home_still_prefers_new_home_demand(self):
        e,home,fleet,_=self.setup_market([80],[100]);ship,cfg=fleet[0]
        old=home['sector'];new=e.sector('new home');e.connect(old,new)
        cfg.update(home=old,bound=False,dedicatedServe=False)
        cfg['origin'].update(home=new,dedicatedServe=False)
        hub=e.hub(old,fleet);e.global_['mwexFleetHubs']=Obj({old:hub})
        e.global_.pop('mwexFleetLock',None);e.roots['this']=Obj(ship=ship,name='pilot')
        receiver=e.station('New receiver',new);source=e.station('New supplier',new,'ai')
        need=e.offer(receiver,e.wares[0],80,110,True);e.offer(source,e.wares[0],80,100,False)
        e.runner.script('mule.fleet.run',{'cfg':cfg})
        self.assertEqual(len(ship['orders']),2);self.assertIs(ship['orders'][1]['offer'],need)
        self.assertNotIn(ship,hub['members'])

    def test_identical_drone_less_ships_each_get_their_own_failure_record(self):
        e,home,fleet,hub=self.setup_market([80],[100,100]);a,ac=fleet[0];b,bc=fleet[1]
        e.global_['mwexDebug']=True
        for ship,cfg in fleet:
            ship['iscapitalship']=True;ship['size']='L';ship['units']['transport']['count']=0
        e.sources[0]['owner']['units']['transport']['count']=0
        self.dispatch(e,hub)
        for ship,cfg in fleet:
            text=cfg['origin']['failure']
            self.assertIn('货运无人机',text);self.assertIn(ship['idcode'],text)
            other=b if ship is a else a;self.assertNotIn(other['idcode'],text)
        self.assertGreaterEqual(e.calls['mule.fleet.plan'],2)

    def test_failure_dedup_recovery_and_no_demand_clear_per_ship(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        e.sources[0]['unitprice']=300
        self.dispatch(e,hub);self.assertIn('实际采购单价',cfg['origin']['failure'])
        logs=len([x for x in e.logs if x.startswith('M4：')])
        self.dispatch(e,hub)
        self.assertEqual(len([x for x in e.logs if x.startswith('M4：')]),logs)
        e.needs[0]['total']=0;self.dispatch(e,hub)
        self.assertNotIn('failure',cfg['origin'])
        e.needs[0]['total']=80;e.sources[0]['unitprice']=50;self.dispatch(e,hub)
        self.assertEqual(len(ship['orders']),2);self.assertNotIn('failure',cfg['origin'])

    def test_ship_journal_explains_tail_with_one_committed_result(self):
        e,home,fleet,hub=self.setup_market([204],[72000]);ship,cfg=fleet[0]
        e.wares[0]['volume']=22
        ship['cargo'][e.wares[0]].update(max=72000//22,free=72000//22)
        cfg['minCargoUsed']=75;cfg['origin']['minCargoUsed']=75
        self.dispatch(e,hub)
        entries=[x for x in e.logbook if x['object'] is ship]
        self.assertEqual(len(entries),2)
        self.assertEqual(sum('实际派单成功' in x['text'] for x in entries),1)
        text='\n'.join(x['text'] for x in entries)
        for expected in ('6.23%','4488','临时解除','连续装载不足','实际派单成功'):
            self.assertIn(expected,text)
        self.assertIn(ship['idcode'],entries[0]['title'])
        self.assertEqual(len(ship['orders']),2);self.assertEqual(ship['orders'][0]['amount'],204)
        self.assertEqual(cfg['minCargoUsed'],75)
        self.assertEqual(e.logs,[]);self.assertNotIn('mule.fleet.trace',e.calls)

    def test_ship_journal_rejections_explain_failed_search_without_burdening_success(self):
        for kind in ('price','corridor'):
            with self.subTest(kind=kind):
                e,home,fleet,hub=self.setup_market([80,10],[1000]);ship,cfg=fleet[0]
                if kind=='price':e.sources[1]['unitprice']=200
                else:
                    outside=e.sector('Outside corridor');e.connect(home['sector'],outside)
                    e.sources[1]['owner']['sector']=outside
                    cfg['corridorDepth']=0;cfg['origin']['corridorDepth']=0
                    hub=e.hub(home,fleet)
                self.dispatch(e,hub)
                entries=[x for x in e.logbook if x['object'] is ship]
                self.assertEqual(len(entries),2);text='\n'.join(x['text'] for x in entries)
                expected='价格检查失败' if kind=='price' else '副单范围排除'
                self.assertEqual(text.count(expected),1)
                self.assertIn('w1',text)
                self.assertIn('90.0 Cr' if kind=='price' else '超出1跳',text)
                self.assertNotIn(expected,entries[-1]['text'])
                self.assertEqual(len(ship['orders']),2)

    def test_ship_journal_mixed_dispatch_lists_actual_legs_only(self):
        e,home,fleet,hub=self.setup_market([350,250,150],[1000]);ship,cfg=fleet[0]
        self.dispatch(e,hub)
        entries=[x for x in e.logbook if x['object'] is ship]
        self.assertEqual(len(entries),1);text=entries[0]['text']
        for ware in e.wares:self.assertIn(ware['name'],text)
        self.assertIn('已创建3',text)
        self.assertEqual(text.count('第'),3);self.assertEqual(len(ship['orders']),6)
        self.assertNotIn('货源/需求设置',text);self.assertNotIn('优先玩家货源',text)
        self.assertEqual(e.logs,[])

    def test_ship_journal_independent_rejections_without_candidate_lists(self):
        e,home,fleet,hub=self.setup_market([80],[100,100])
        for ship,cfg in fleet:ship['macro']='same'
        hub=e.hub(home,fleet);e.sources[0]['unitprice']=300
        self.dispatch(e,hub)
        for ship,cfg in fleet:
            entries=[x for x in e.logbook if x['object'] is ship]
            self.assertEqual(len(entries),1);self.assertIn(ship['idcode'],entries[0]['title'])
            self.assertEqual(entries[0]['text'].count('价格检查失败'),1)
        self.assertEqual(e.calls['mule.fleet.plan'],2)
        self.assertNotIn('mule.fleet.trace',e.calls);self.assertEqual(e.created,0)

    def test_idle_journal_cooldown_survives_price_and_quantity_changes(self):
        e,home,fleet,hub=self.setup_market([80],[100]);e.sources[0]['unitprice']=300
        self.dispatch(e,hub);before=len(e.logbook)
        for price in (301,302,310):
            e.sources[0]['unitprice']=price;e.sources[0]['total']-=1
            self.dispatch(e,hub);self.assertEqual(len(e.logbook),before)
        e.player['age']+=601;self.dispatch(e,hub);self.assertEqual(len(e.logbook),before+1)
        fleet[0][1]['signature']='edited-settings'
        self.dispatch(e,hub);self.assertEqual(len(e.logbook),before+2)

    def test_ship_journal_failed_commit_never_claims_actual_dispatch_success(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0];e.fail_at=1
        self.dispatch(e,hub)
        committed=[x for x in e.logbook if x['object'] is ship and '提交失败' in x['title']]
        self.assertEqual(len(committed),1);text=committed[0]['text']
        self.assertIn('提交失败',text);self.assertNotIn('实际派单成功',text)
        self.assertIn('组合：',text);self.assertEqual(ship['orders'],[])
        self.dispatch(e,hub)
        self.assertEqual(len([x for x in e.logbook if '提交失败' in x['title']]),1)

    def test_debug_cannot_change_trade_queries_orders_or_ship_log_size(self):
        results=[]
        for debug in (False,True):
            e,home,fleet,hub=self.setup_market([350,250,150,1],[1000])
            e.global_['mwexDebug']=debug
            self.dispatch(e,hub);ship,cfg=fleet[0]
            results.append(([(o['offer']['ware']['name'],o['amount'],o['offer']['isbuyoffer']) for o in ship['orders']],dict(e.queries),len(e.logbook)))
            if debug:
                self.assertGreater(e.calls['mule.fleet.plan'],0)
                self.assertTrue(e.disk_logs)
                self.assertTrue(all(x['name'] in ('m4-lifecycle.txt','m4-search.txt','m4-orders.txt','m4-economy.txt') and x['directory']=='MulesExtended' for x in e.disk_logs))
                self.assertTrue(any('ship=ship0' in x['text'] and 'relaxed=' in x['text'] for x in e.disk_logs))
                before=len(e.disk_logs);e.global_['mwexDebug']=False
                e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=True))
                self.assertEqual(len(e.disk_logs),before)
            else:
                self.assertEqual(e.disk_logs,[]);self.assertNotIn('mule.fleet.trace',e.calls)
        self.assertEqual(*results)

    def test_many_candidates_produce_one_summary_and_exact_price_metrics(self):
        e,home,fleet,hub=self.setup_market([80,10],[1000]);ship,cfg=fleet[0]
        e.sources[1]['unitprice']=200
        for n in range(80):
            station=e.station('Rejected'+str(n),home['sector'],'ai')
            e.offer(station,e.wares[1],10,111+n,False)
        hub=e.hub(home,fleet)
        result=e.runner.script('mule.fleet.plan',dict(hub=hub,cfg=cfg,relaxed=False))
        d=result['diagnostic']
        self.assertGreater(d['price'],0);self.assertLess(d['price'],81)
        self.assertEqual(d['minGap'],1);self.assertEqual(d['gapWare'],e.wares[1])
        self.assertNotIn('lines',d);self.assertNotIn('seen',d)
        self.dispatch(e,hub)
        self.assertLessEqual(len(e.logbook),2);self.assertTrue(all(len(x['text'])<2400 for x in e.logbook))
        self.assertEqual(e.logs,[])

    def test_ship_journal_english_is_localized(self):
        e,home,fleet,hub=self.setup_market([80,10],[1000],language='en');ship,cfg=fleet[0]
        e.sources[1]['unitprice']=200;self.dispatch(e,hub)
        entries=[x for x in e.logbook if x['object'] is ship]
        self.assertEqual(len(entries),2)
        for entry in entries:self.assertNotRegex(entry['title']+entry['text'],r'[\u4e00-\u9fff]')
        text='\n'.join(x['text'] for x in entries)
        self.assertIn('price checks',text);self.assertIn('Minimum load waived',text)
        self.assertNotIn('text:61552:',text)

    def test_options_menu_toggles_disk_debug_without_changing_ship_config(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        md=ET.parse(SRC/'md/mwex_options.xml').getroot()
        api=Obj({name:name for name in ('Register_Options_Menu','Add_Row','Make_CheckBox','Make_Text')})
        e.roots.update(md=Obj(Simple_Menu_API=api),Open='options-open',DebugChanged='debug-changed')
        def cue(name):e.runner.children(list(md.find('./cues/cue[@name="'+name+'"]/actions')), {})
        cue('Register');cue('Open')
        check=next(s['param'] for s in e.cue_signals if s['cue']=='Make_CheckBox' and s['param']['id']=='mwex_debug')
        self.assertFalse(check['checked']);self.assertEqual(check['onClick'],'debug-changed')
        e.roots['event']=Obj(param=Obj(checked=1));cue('DebugChanged')
        self.assertTrue(e.global_['mwexDebug']);cue('Open')
        check=next(s['param'] for s in reversed(e.cue_signals) if s['cue']=='Make_CheckBox' and s['param']['id']=='mwex_debug')
        self.assertTrue(check['checked'])
        self.assertEqual(e.queries,{});self.assertIs(ship['pilot']['mwexFleetCfg'],cfg)
        e.roots['event']=Obj(param=Obj(checked=0));cue('DebugChanged')
        self.assertFalse(e.global_['mwexDebug'])
        e.global_['mwexFleetHubs']=Obj({home:hub})
        e.runner.children(list(ET.parse(SRC/'md/mwex_capacity.xml').getroot().find('./cues/cue[@name="Loaded"]/actions')), {})
        self.assertFalse(e.global_['mwexDebug'])

    def test_load_removes_legacy_full_report_caches_but_keeps_history_and_orders(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        self.dispatch(e,hub);history=list(e.logbook);orders=list(ship['orders'])
        ship['pilot']['mwexFleetJournalLast']=Obj(body='old'*10000)
        ship['pilot']['mwexFleetJournalSerial']=3
        e.global_['mwexFleetHubs']=Obj({home:hub});e.global_['mwexDebug']=True
        md=ET.parse(SRC/'md/mwex_capacity.xml').getroot()
        e.runner.children(list(md.find('./cues/cue[@name="Loaded"]/actions')), {})
        self.assertNotIn('mwexFleetJournalLast',ship['pilot'])
        self.assertNotIn('mwexFleetJournalSerial',ship['pilot'])
        self.assertEqual(e.logbook,history);self.assertEqual(ship['orders'],orders)
        self.assertTrue(e.global_['mwexDebug'])

    def test_commit_failure_returns_concrete_reason_for_ship_diagnostics(self):
        e,home,fleet,hub=self.setup_market([80],[100]);ship,cfg=fleet[0]
        plan=e.runner.script('mule.fleet.plan',{'hub':hub,'cfg':cfg,'relaxed':False})['plan']
        home['cargo']['free']['container']=1
        result=e.runner.script('mule.fleet.commit',{'cfg':cfg,'plan':plan})
        self.assertFalse(result['ok']);self.assertIn('共享货仓',result['failure'])
        e.runner.script('mule.fleet.report',{'cfg':cfg,'reason':result['failure']})
        self.assertIn('共享货仓',cfg['origin']['failure']);self.assertEqual(ship['orders'],[])


def syntax_check():
    attributes={'exact','value','in','name','amount','object','offer','tradeoffer','buyer','seller',
                'wares','resultdistance','refobject','useknownpath','uselocalhighways','param2','param3',
                'tradepartner','trade','order','text','title','default','directory','result','variable'}
    count=0
    for p in (SRC/'aiscripts').glob('mule.fleet.*.xml'):
        root=ET.parse(p).getroot()
        for node in root.iter():
            for key,value in node.attrib.items():
                if key not in attributes:continue
                if key=='name' and node.tag in ('aiscript','param','label','retval','save_retval'):continue
                if key=='result' and not value.startswith('$'):continue
                try:Parser(value).parse()
                except Exception as error:raise AssertionError(f'{p.name} {node.tag} {key}={value}: {error}') from error
                count+=1
    return count


if __name__=='__main__':
    count=syntax_check()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(XMLDispatchChecks)
    scheduler=runpy.run_path(str(HERE/'test_scheduler.py'),init_globals=globals())
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(scheduler['SchedulerChecks']))
    observation=runpy.run_path(str(HERE/'test_m4_observation.py'))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(observation['M4ObservationChecks']))
    # Keep regressions for the other behaviors and the public M4 load slider.
    old_script=HERE/'test_legacy.py'
    saved_argv=sys.argv[:]
    try:
        sys.argv=[str(old_script),str(SRC)]
        previous=runpy.run_path(str(old_script))
    finally:sys.argv=saved_argv
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(previous['MuleFixes']))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'new_ai_expressions_parsed':count,
            'source_sha256':{str(p.relative_to(SRC)):hashlib.sha256(p.read_bytes()).hexdigest() for p in SRC.rglob('*.xml')},
            'limits':'Actual XML on synthetic adapter; no native engine scheduling, paths, reservation timing, save or FPS verification.'}
    (HERE/'checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    raise SystemExit(not result.wasSuccessful())
