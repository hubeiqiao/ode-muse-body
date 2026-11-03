from pathlib import Path
import hashlib,json,os
import esptool
ROOT=Path(__file__).resolve().parents[1]
port=os.environ.get('ESP32_SERIAL_PORT')
if not port:raise SystemExit('Set ESP32_SERIAL_PORT to the explicitly verified board port')
size=int(os.environ.get('ESP32_FLASH_BYTES','0x400000'),0);chunk=0x10000
(ROOT/'backups').mkdir(exist_ok=True)
output=ROOT/'backups/iphone-board-original-4mb.bin'
assert not output.exists(),'Preserve existing backup; do not overwrite'
esp=None
try:
    with output.open('wb') as f:
        for offset in range(0,size,chunk):
            for attempt in range(1,5):
                try:
                    if esp is None:
                        esp=esptool.detect_chip(port);esp=esp.run_stub();esp.change_baud(230400)
                    data=esp.read_flash(offset,chunk)
                    assert len(data)==chunk
                    f.write(data);f.flush()
                    print(f'VERIFIED_BLOCK {offset+len(data)}/{size}',flush=True)
                    break
                except Exception as exc:
                    print(f'RETRY {offset:#x} {attempt}: {exc}',flush=True)
                    if esp is not None:esp._port.close();esp=None
                    if attempt==4:raise
    data=output.read_bytes();md5=hashlib.md5(data).hexdigest()
    chip_md5=esp.flash_md5sum(0,size)
    if isinstance(chip_md5,bytes):chip_md5=chip_md5.decode()
    assert md5==chip_md5.lower(),(md5,chip_md5)
    result={'port':port,'bytes':len(data),'md5_verified':True,'md5':md5,'sha256':hashlib.sha256(data).hexdigest()}
    (ROOT/'backups/iphone-board-original-4mb.json').write_text(json.dumps(result,indent=2))
    print('BACKUP_COMPLETE '+json.dumps(result),flush=True)
finally:
    if esp is not None:esp.hard_reset();esp._port.close()
