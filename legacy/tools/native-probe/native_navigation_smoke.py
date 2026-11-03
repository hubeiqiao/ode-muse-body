"""One safe, ID-based Back navigation in Settings. No network-setting mutations."""
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
    start=time.monotonic();reads=[];pressed=False;ack=None;effect=None
    async with NativeRemotedTunnel(serial=device['hardwareProperties']['udid']) as rsd:
        async with AccessibilityAudit(rsd) as audit:
            connect_s=time.monotonic()-start
            async def read():
                t=time.monotonic();rows=[]
                async for e in audit.iter_elements():
                    rows.append(e.to_dict())
                    if len(rows)>=50:break
                item={'seconds':round(time.monotonic()-t,4),'rows':rows};reads.append(item);return rows
            try:
                first=await asyncio.wait_for(read(),12)
                second=await asyncio.wait_for(read(),12)
                labels=[e.get('caption') or '' for e in second]
                # Exact native Back button on the already-verified Wi-Fi page.
                target=next((e for e in second if e.get('caption')=='Settings, Button'),None)
                if target and any(x=='Wi-Fi, Header' for x in labels) and any(x.startswith('Example WiFi, Secure Network') for x in labels):
                    t=time.monotonic();await audit.perform_press(bytes.fromhex(target['platform_identifier']));ack=time.monotonic()-t;pressed=True
                    await asyncio.sleep(.25)
                    after=await asyncio.wait_for(read(),15)
                    after_labels=[e.get('caption') or '' for e in after]
                    effect=('Wi-Fi, Header' not in after_labels and any('Settings' in x and 'Header' in x for x in after_labels))
                    effect_s=time.monotonic()-t
                else:effect_s=None
            finally:await audit.set_app_monitoring_enabled(False)
    result={'source':'ios_accessibility_audit','connect_s':round(connect_s,4),'reads':reads,'pressed_only_settings_back':pressed,'press_ack_s':round(ack,4) if ack is not None else None,'effect_verified_from_native_text':effect,'effect_verify_s':round(effect_s,4) if effect_s is not None else None,'total_s':round(time.monotonic()-start,4),'screenshots_used':0}
    p=pathlib.Path('work/native-probe/native-navigation.json');p.write_text(json.dumps(result,indent=2));p.chmod(0o600)
    print(json.dumps({k:v for k,v in result.items() if k!='reads'}));print('read summaries',[(r['seconds'],len(r['rows'])) for r in reads])
asyncio.run(main())
