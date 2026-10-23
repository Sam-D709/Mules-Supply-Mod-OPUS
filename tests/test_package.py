"""Verify the release artifact through actual extraction and archive readback."""
from contextlib import redirect_stdout
from pathlib import Path
import hashlib
import importlib.util
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_build',ROOT/'tools/build.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)


class ReleasePackageChecks(unittest.TestCase):
    def test_release_extracts_as_one_complete_mod_with_matching_runtime_sources(self):
        with tempfile.TemporaryDirectory(prefix='mules-package-') as temporary:
            build.OUT=Path(temporary)/'dist'
            with redirect_stdout(io.StringIO()):summary=build.main()
            public_version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
            self.assertEqual(summary['public_version'],public_version)
            self.assertEqual(summary['tag'],'v'+public_version)
            self.assertEqual(summary['package'],f'Mules-Supply-Mod-OPUS-v{public_version}.zip')
            self.assertEqual(summary['extension_version'],ET.parse(ROOT/'content.xml').getroot().get('version'))
            package=build.OUT/summary['package']
            extensions=Path(temporary)/'X4 Foundations'/'extensions';extensions.mkdir(parents=True)
            with zipfile.ZipFile(package) as archive:archive.extractall(extensions)
            self.assertEqual([p.name for p in extensions.iterdir()],['mules_and_warehouses_extended'])
            installed=extensions/'mules_and_warehouses_extended'
            for name in ('content.xml','README.md','README.en.md','LICENCE.md','ATTRIBUTION.md','VERSION','ext_01.cat','ext_01.dat'):
                self.assertEqual((installed/name).read_bytes(),(ROOT/name).read_bytes() if name not in ('ext_01.cat','ext_01.dat') else (build.OUT/name).read_bytes())
            self.assertEqual(ET.parse(installed/'content.xml').getroot().get('id'),'ws_2013772865')
            payload=(installed/'ext_01.dat').read_bytes();offset=0;runtime={}
            for line in (installed/'ext_01.cat').read_text(encoding='utf-8').splitlines():
                name,size,stamp,checksum=line.rsplit(' ',3);size=int(size)
                data=payload[offset:offset+size];offset+=size
                self.assertEqual(hashlib.md5(data).hexdigest(),checksum)
                runtime[name]=data;ET.fromstring(data)
            self.assertEqual(offset,len(payload))
            expected={p.relative_to(ROOT).as_posix():p.read_bytes()
                      for directory in build.RUNTIME for p in (ROOT/directory).glob('*.xml')}
            expected['t/0001-l044.xml']=expected['t/0001.xml']
            self.assertEqual(runtime,expected)
            self.assertEqual(runtime['t/0001-l044.xml'],runtime['t/0001.xml'])
            self.assertIn('t/0001-l086.xml',runtime)
            self.assertIn('md/mwex_options.xml',runtime)
            self.assertIn('aiscripts/mule.fleet.summary.xml',runtime)
            for name in ('t/0001-l044.xml','t/0001-l086.xml'):
                texts={t.get('id'):t.text for t in ET.fromstring(runtime[name]).iter('t')}
                self.assertIn('4401',texts);self.assertIn('-scriptlogfiles',texts['4402'])
            dependency=ET.parse(installed/'content.xml').getroot().find('dependency[@id="ws_2042901274"]')
            self.assertIsNotNone(dependency);self.assertEqual(dependency.get('optional'),'true')
            checksum=(build.OUT/'SHA256SUMS.txt').read_text(encoding='utf-8').split()
            self.assertEqual(checksum,[hashlib.sha256(package.read_bytes()).hexdigest(),package.name])

    def test_identical_sources_produce_identical_release_bytes(self):
        with tempfile.TemporaryDirectory(prefix='mules-repro-') as temporary:
            artifacts=[]
            for directory in ('first','second'):
                build.OUT=Path(temporary)/directory
                with redirect_stdout(io.StringIO()):summary=build.main()
                artifacts.append([(build.OUT/name).read_bytes() for name in (summary['package'],'ext_01.cat','ext_01.dat','SHA256SUMS.txt')])
            self.assertEqual(*artifacts)


if __name__=='__main__':unittest.main(verbosity=2)
