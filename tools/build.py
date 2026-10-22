"""Build a reproducible X4 CAT/DAT pair and installable ZIP from this baseline."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dist'
RUNTIME=('aiscripts','md','libraries','t')


def main():
    content=ET.parse(ROOT/'content.xml').getroot()
    assert content.get('name')=='Mules-Supply-Mod-OPUS'
    sources={p.relative_to(ROOT).as_posix():p.read_bytes()
             for directory in RUNTIME for p in sorted((ROOT/directory).rglob('*.xml'))}
    assert sources,'No runtime sources'
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
    for name in ('README.md','LICENCE.md','ATTRIBUTION.md'):install[name]=(ROOT/name).read_bytes()
    for p in sorted((ROOT/'docs').rglob('*')):
        if p.is_file():install[p.relative_to(ROOT).as_posix()]=p.read_bytes()
    package=OUT/'Mules-Supply-Mod-OPUS-baseline.zip'
    with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(install.items()):
            entry=zipfile.ZipInfo('mules_and_warehouses_extended/'+name,date_time=(2026,10,22,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644 << 16
            archive.writestr(entry,data)
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        assert {n.removeprefix('mules_and_warehouses_extended/'):archive.read(n) for n in archive.namelist()}==install
    summary={'project':content.get('name'),'extension_version':content.get('version'),
             'runtime_entries':len(sources),
             'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (OUT/'ext_01.cat',OUT/'ext_01.dat',package)}}
    (OUT/'build.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
