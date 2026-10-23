"""Source-driven boundary checks, not an emulation of the X4 runtime."""
from pathlib import Path
import json
import re
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
MOD = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else ROOT.parent


def script(name):
    return ET.parse(MOD / 'aiscripts' / (name + '.xml')).getroot()


def evaluate(expression, values):
    """Interpret only the arithmetic/boolean subset used by these assertions.

    Reads the real XML expressions, including list extrema and integer casts.
    Engine offer discovery, prices, reservations and execution are not emulated.
    """
    names = {}
    for n, key in enumerate(sorted(values, key=len, reverse=True)):
        name = 'v' + str(n)
        if key in expression:
            expression = expression.replace(key, name)
            names[name] = values[key]
    assert '$' not in expression, expression
    expression = re.sub(r'\b(gt|lt|ge|le)\b', lambda m: {'gt': '>', 'lt': '<', 'ge': '>=', 'le': '<='}[m[0]], expression)
    expression = re.sub(r'(\d)Cr\b', r'\1', expression)
    expression = expression.replace('true', 'True').replace('false', 'False')
    if expression.startswith('if '):
        cond, rest = expression[3:].split(' then ', 1)
        yes, no = rest.split(' else ', 1)
        expression = f'({yes} if {cond} else {no})'

    def postfix(text):
        result = ''
        i = 0
        while i < len(text):
            char = text[i]
            if char not in '([':
                result += char
                i += 1
                continue
            stack = [char]
            j = i + 1
            while stack:
                if text[j] in '([':
                    stack.append(text[j])
                elif text[j] in ')]':
                    assert (stack.pop(), text[j]) in [('(', ')'), ('[', ']')]
                j += 1
            part = char + postfix(text[i+1:j-1]) + text[j-1]
            if text[j:j+4] in ('.min', '.max'):
                part = text[j+1:j+4] + '(' + part + ')'
                j += 4
            if text[j:j+1] in ('i', 'f'):
                part = ('int' if text[j] == 'i' else 'float') + '(' + part + ')'
                j += 1
            result += part
            i = j
        return result

    return eval(postfix(expression), {'__builtins__': {}, 'min': min, 'max': max, 'int': int, 'float': float}, names)


def assignment(tree, name):
    found = tree.findall(f'.//set_value[@name="{name}"]')
    assert len(found) == 1, (name, len(found))
    return found[0].get('exact')


class MuleFixes(unittest.TestCase):
    def test_intermediate_inventory_threshold(self):
        tree = script('station_mule')
        fullness = assignment(tree, '$targetStockLevel')
        condition = next(x.get('chance') for x in tree.iter('continue') if '$targetStockLevel' in x.get('chance', ''))
        for count, target, skipped in [(10,100,False), (20,100,False), (30,100,False), (31,100,True), (80,100,True), (0,0,False)]:
            level = evaluate(fullness, {'$supplyTarget.owner.cargo.{$supplyTarget.ware}.count':count, '$supplyTarget.owner.cargo.{$supplyTarget.ware}.target':target, '$supplyTarget.stocklevel':1-count/max(target,1)})
            self.assertEqual(evaluate(condition, {'$targetStockLevel':level, '$targetIsIntermediate':True}) > 0, skipped)
        self.assertEqual(evaluate(condition, {'$targetStockLevel':.8, '$targetIsIntermediate':False}), 0)

    def test_illegal_only(self):
        block = script('station_mule').find('.//do_if[@value="$drugsOnly"]')
        def rejected(node, ware):
            for child in node:
                if child.tag == 'do_if':
                    values = {'$someOuter.ware.id':ware}
                    expr = child.get('value')
                    if child.get('exact'):
                        expr = '(' + expr + ') == (' + child.get('exact') + ')'
                    if evaluate(expr, values) and rejected(child, ware):
                        return True
                if child.tag == 'set_value' and child.get('name') == '$wareName':
                    return child.get('exact') == "'NOPE'"
            return False
        for ware in ['spacefuel', 'spaceweed', 'majadust', 'stimulants']:
            self.assertFalse(rejected(block, ware))
        for ware in ['energycells', 'ore', 'silicon', 'hullparts']:
            self.assertTrue(rejected(block, ware))






    def test_custom_target_unlimited_and_blacklists(self):
        tree = script('travel_mule')
        loop = tree.find('.//do_all[@counter="$customer"]')
        for limit, distance, allowed in [(14,14,True),(14,15,False),(15,16,True),(15,40,True)]:
            first = loop.find('do_if')
            if first is not None:
                selected = first if evaluate(first.get('value'), {'$maxDist':limit}) else loop.find('do_else')
            else:
                selected = loop
            find = selected.find('find_buy_offer')
            bound = find.find('.//match_gate_distance')
            permitted = 'max' not in bound.attrib or distance <= evaluate(bound.get('max'),{'$maxDist':limit})
            self.assertEqual(permitted, allowed)
            self.assertIsNotNone(bound.find('blacklist'))
            self.assertEqual(len(find.findall('.//match_use_blacklist')),3)


    def test_minimum_slider_and_unchanged_default(self):
        param = script('supply_mule').find('.//param[@name="minCargoUsed"]')
        self.assertEqual(param.find('input_param[@name="min"]').get('value'),'0')
        self.assertEqual(param.get('default'),'75')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MuleFixes)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    output = {'source':str(MOD), 'tests':result.testsRun, 'failures':len(result.failures), 'errors':len(result.errors),
        'scope':'XML-derived arithmetic, branching and integration assertions; no game runtime test'}
    (ROOT / ('mules_regression_original.json' if MOD.name != 'mules_fixed' else 'mules_regression_fixed.json')).write_text(json.dumps(output,indent=2),encoding='utf-8')
    sys.exit(not result.wasSuccessful())
