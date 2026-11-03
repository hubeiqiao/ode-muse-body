"""Read paired iPhone AX focus elements; never activate, type, or change settings."""
import asyncio,json,time,pathlib,os
from pymobiledevice3.remote.native_tunnel import NativeRemotedTunnel
from pymobiledevice3.services.accessibilityaudit import AccessibilityAudit

async def main():
    selected=os.environ.get('NATIVE_DEVICE_UDID')
    if not selected:raise SystemExit('Set NATIVE_DEVICE_UDID explicitly; no device is auto-selected')
    root=pathlib.Path(__file__).resolve().parents[2]
    inventory=pathlib.Path(os.environ.get('NATIVE_INVENTORY',root/'runtime/native/devices.json'))
    devices=json.loads(inventory.read_text())['result']['devices']
    matches=[d for d in devices if d.get('hardwareProperties',{}).get('udid')==selected]
    if len(matches)!=1:raise SystemExit('Selected UDID must occur exactly once in the supplied inventory')
    device=matches[0]
    started=time.monotonic();rows=[];error=None;ready_s=None
    async with NativeRemotedTunnel(serial=device['hardwareProperties']['udid']) as rsd:
        async with AccessibilityAudit(rsd) as audit:
            ready_s=time.monotonic()-started
            async def collect():
                async for item in audit.iter_elements():
                    row=item.to_dict();row['elapsed_s']=round(time.monotonic()-started,4);rows.append(row)
                    if len(rows)>=40:break
            try:
                await asyncio.wait_for(collect(),12)
            except (TimeoutError,Exception) as exc:
                error=type(exc).__name__+': '+str(exc)
            finally:
                await audit.set_app_monitoring_enabled(False)
    result={'source':'ios_accessibility_audit','screenshot_used':False,'ready_s':round(ready_s,4),'total_s':round(time.monotonic()-started,4),'rows':rows,'error':error,'activated_elements':0}
    path=pathlib.Path('work/native-probe/native-elements.json');path.write_text(json.dumps(result,indent=2));path.chmod(0o600)
    print(json.dumps({'source':result['source'],'ready_s':result['ready_s'],'total_s':result['total_s'],'count':len(rows),'error':error,'captions':[r['caption'] for r in rows[:14]]}))
asyncio.run(main())
