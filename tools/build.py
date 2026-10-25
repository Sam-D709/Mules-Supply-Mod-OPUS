"""Build and extract-verify a reproducible, drag-and-drop X4 release ZIP."""
from pathlib import Path
import hashlib
import json
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dist'
RUNTIME=('aiscripts','md','libraries','t')
INSTALL_FOLDER='mules_supply_mod_opus'
EXTENSION_ID='mules_supply_mod_opus'


def verify_install(package, expected):
    """Exercise the user's unzip-then-copy installation layout outside the live game."""
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        prefix=INSTALL_FOLDER+'/'
        assert all(n.startswith(prefix) and '..' not in n.split('/') and '\\' not in n
                   for n in archive.namelist()),'Unexpected installation path'
        assert len(archive.namelist())==len(set(archive.namelist())),'Duplicate ZIP entry'
        assert {n.removeprefix(prefix):archive.read(n) for n in archive.namelist()}==expected
        with tempfile.TemporaryDirectory(prefix='install-check-',dir=package.parent) as temporary:
            extensions=Path(temporary)/'extensions';extensions.mkdir()
            archive.extractall(extensions)
            assert [p.name for p in extensions.iterdir()]==[INSTALL_FOLDER]
            installed=extensions/INSTALL_FOLDER
            metadata=ET.parse(installed/'content.xml').getroot()
            assert metadata.get('id')==EXTENSION_ID
            assert not metadata.get('id').startswith('ws_')
            assert metadata.get('lastupdate') is None
            assert metadata.get('name')=='Mules-Supply-Mod-OPUS'
            assert (installed/'ext_01.cat').is_file() and (installed/'ext_01.dat').is_file()
            assert (installed/'README.md').is_file() and (installed/'README.en.md').is_file()
            assert (installed/'VERSION').is_file()
            assert not (installed/'extensions').exists(),'Extra extensions nesting'


def main():
    content=ET.parse(ROOT/'content.xml').getroot()
    assert content.get('name')=='Mules-Supply-Mod-OPUS'
    assert content.get('id')==EXTENSION_ID
    assert content.get('lastupdate') is None
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:alpha|beta|rc)\.[1-9]\d*)?',version), 'Invalid public VERSION'
    sources={p.relative_to(ROOT).as_posix():p.read_bytes()
             for directory in RUNTIME for p in sorted((ROOT/directory).rglob('*.xml'))}
    assert sources,'No runtime sources'
    # Explicit English locale comes from the default English source, with no second translation to drift.
    sources['t/0001-l044.xml']=sources['t/0001.xml']
    lines=[];chunks=[]
    for name,data in sorted(sources.items()):
        ET.fromstring(data)
        lines.append(f'{name} {len(data)} 0 {hashlib.md5(data).hexdigest()}')
        chunks.append(data)
    cat=('\n'.join(lines)+'\n').encode('utf-8');dat=b''.join(chunks)
    # Verify every byte and offset, not merely whether the ZIP opens.
    offset=0
    for line in cat.decode('utf-8').splitlines():
        name,size,stamp,checksum=line.rsplit(' ',3);size=int(size)
        payload=dat[offset:offset+size];offset+=size
        assert payload==sources[name] and hashlib.md5(payload).hexdigest()==checksum
    assert offset==len(dat)
    OUT.mkdir(exist_ok=True)
    (OUT/'ext_01.cat').write_bytes(cat);(OUT/'ext_01.dat').write_bytes(dat)
    install={'ext_01.cat':cat,'ext_01.dat':dat,'content.xml':(ROOT/'content.xml').read_bytes()}
    for name in ('README.md','README.en.md','LICENCE.md','ATTRIBUTION.md','VERSION'):install[name]=(ROOT/name).read_bytes()
    for p in sorted((ROOT/'docs').rglob('*')):
        if p.is_file():install[p.relative_to(ROOT).as_posix()]=p.read_bytes()
    package=OUT/f'Mules-Supply-Mod-OPUS-v{version}.zip'
    date_time=tuple(map(int,content.get('date').split('-')))+(0,0,0)
    with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(install.items()):
            entry=zipfile.ZipInfo(INSTALL_FOLDER+'/'+name,date_time=date_time)
            entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644 << 16
            archive.writestr(entry,data)
    verify_install(package,install)
    package_sha=hashlib.sha256(package.read_bytes()).hexdigest()
    (OUT/'SHA256SUMS.txt').write_text(f'{package_sha}  {package.name}\n',encoding='utf-8',newline='\n')
    summary={'project':content.get('name'),'extension_version':content.get('version'),
             'public_version':version,'tag':'v'+version,
             'runtime_entries':len(sources),'package':package.name,'install_folder':INSTALL_FOLDER,
             'extension_id':EXTENSION_ID,
             'extracted_install_verified':True,
             'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (OUT/'ext_01.cat',OUT/'ext_01.dat',package)}}
    (OUT/'build.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))
    return summary


if __name__=='__main__':main()
