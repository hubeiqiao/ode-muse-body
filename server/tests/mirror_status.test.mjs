import test from 'node:test';
import assert from 'node:assert/strict';
import {mirrorMessage,mirrorAge,modelRoute} from '../../companion-ui/src/device-status.mjs';

test('remote session and local transport have distinct explanations',()=>{
 const source={screen:false,device:true,mirror_diagnostics:{state:'source_disconnected',receiver_stream_connected:true,frame_age_s:591.9}};
 assert.match(mirrorMessage(source),/AirPlay session disconnected/);
 assert.equal(mirrorAge(source),'Last frame received 591s ago.');
 assert.match(mirrorMessage({...source,mirror_diagnostics:{state:'receiver_heartbeat_lost'}}),/local screen stream/);
 assert.match(mirrorMessage({...source,mirror_diagnostics:{state:'waiting_for_first_frame'}}),/first new frame/);
});
test('legacy/no status does not invent a confirmed session fault',()=>{
 assert.doesNotMatch(mirrorMessage({screen:false,mirror_diagnostics:{receiver_stream_connected:true}}),/session disconnected/);
 for(const age of [null,NaN,Infinity,-1,'42']) assert.equal(mirrorAge({mirror_diagnostics:{frame_age_s:age}}),'No frame received.');
});
test('model labels follow actual runtime rather than a hardcoded Haiku backup',()=>{
 const label=modelRoute({models:{judge:'jev-latest',backup:'muse-spark-1.3'}});
 assert.match(label,/jev-latest.*muse-spark-1.3/);assert.doesNotMatch(label,/Haiku/);
 assert.equal(modelRoute(null),'Checking control models');
});
