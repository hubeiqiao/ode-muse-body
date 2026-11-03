"""Actual local MCP client -> existing server -> ESP32 -> iPhone test.

This verifies tool transport, NOT microphone/transcription or desktop voice UI.
"""
import asyncio,base64,json,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'runtime/iphone/voice/transport'
OUT.mkdir(parents=True,exist_ok=True)

async def main():
 report={'microphone_test':'pending human activation','transport':'stdio MCP -> localhost:8894 -> ESP32 BLE','steps':[]}
 params=StdioServerParameters(command=str(ROOT/'server/.venv/bin/python'),
  args=['-m','hidagent.cli','--url','http://127.0.0.1:8894','mcp'],
  env={'PYTHONPATH':str(ROOT/'server')},cwd=str(ROOT/'server'))
 async with stdio_client(params) as (read,write),ClientSession(read,write) as session:
  await session.initialize()
  report['tools']=[t.name for t in (await session.list_tools()).tools]
  for name,goal in [('open-calculator','Open Calculator'),('focus-search','Open Search'),('type-english','type Voice73')]:
   t=time.perf_counter()
   result=await session.call_tool('iphone_command',{'goal':goal,'wait_seconds':20})
   receipt=None
   for content in result.content:
    if content.type=='image':(OUT/(name+'.jpg')).write_bytes(base64.b64decode(content.data))
    if content.type=='text':
     try:
      obj=json.loads(content.text)
      if isinstance(obj,dict) and 'status' in obj:receipt=obj
     except ValueError:pass
   row={'goal':goal,'wall_s':round(time.perf_counter()-t,4),'tool_error':result.isError,'receipt':receipt}
   report['steps'].append(row);(OUT/'result.json').write_text(json.dumps(report,indent=2))
   print(json.dumps({'goal':goal,'wall_s':row['wall_s'],'tool_error':result.isError,'status':receipt.get('status') if receipt else None}),flush=True)
   if not receipt or receipt['status']!='completed':break # no automatic input retry
 report['actual_screenshot_review']='pending'
 (OUT/'result.json').write_text(json.dumps(report,indent=2))
asyncio.run(main())
