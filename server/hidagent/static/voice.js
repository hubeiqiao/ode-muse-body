/* One Gemini Live conversation, continuously receiving the mirrored iPad screen. */
(() => {
  const el = id => document.getElementById(id);
  const zh = () => document.documentElement.lang.startsWith('zh');
  const label = (cn, en) => zh() ? cn : en;
  let ws, session, mic, output, stream, source, processor, muted = false, active = false;
  let epoch = 0, nextAudio = 0, timer, setupTimer, pending = 0;
  const playing = new Set(), calls = new Map();
  let lastReceiptSummary = "";
  let stopLatched = false, controlVersion = 0, inputText = "", lastInputAt = 0, newInput = true;
  let controlReady = Promise.resolve(), inputVersion = 0, intentReported = false;
  let ownerInputTurn = 0, demoReadback = null;
  const cancelledCalls = new Set();
  // Companion conversation continuity: finalized transcript text only (no audio, no images) goes to the
  // local companion log on this Mac, so a later conversation can continue where it left off.
  let ownerTurn = '', miloTurn = '', turnsFlushed = Promise.resolve();
  const CONVERSATION_FLUSH_WAIT_MS = 1500;
  let screenTimer = null;
  const taskWatches = new Map(), watchedTasks = new Set();
  const watchedCalls = new Set();
  const TASK_INTERVAL_MS = 1500;
  const taskRunning = r => r && (r.running === true || r.terminal === false ||
    (['running','in_progress','started','busy','stopping'].includes(r.status) && r.terminal !== true));
  function stopTaskWatch() {
    for (const watch of taskWatches.values()) clearTimeout(watch.timer);
    taskWatches.clear();
  }
  function compactReceipt(receipt) {
    // Only result text/evidence reaches Live, never full receipts, frames or audio.
    const keys = ['task_id','request_id','status','speech','elapsed_s','metrics_line','answer','summary','finding','findings','evidence','reason','error','claim','quote',
      'url','source_url','source','app','author','title','result','last_action','research','excerpts','content','attribution','source_reference','proposal','stop_summary','line',
      'draft_id','readback','readback_required','text','owner_notes_after_task'];
    function pick(value, depth = 0) {
      if (typeof value === 'string') return value.slice(0, 1000);
      if (typeof value === 'number' || typeof value === 'boolean' || value === null) return value;
      if (depth > 5) return undefined;
      if (Array.isArray(value)) return value.slice(0, 6).map(v => pick(v, depth + 1));
      if (value && typeof value === 'object') {
        const result = {};
        for (const key of keys) if (value[key] !== undefined) result[key] = pick(value[key], depth + 1);
        return result;
      }
    }
    return pick(receipt);
  }
  function watchTask(call, receipt, generation, sid) {
    if (!taskRunning(receipt) || watchedCalls.has(call.id)) return;
    // A fallback model status call/busy receipt belongs to the same running task.
    const key = receipt.task_watch_id || receipt.request_id || receipt.task_id ||
      (taskWatches.size && (call.name === 'phone_status' || receipt.status === 'busy')
        ? taskWatches.keys().next().value : call.id);
    watchedCalls.add(call.id);
    if (watchedTasks.has(key)) return;
    watchedTasks.add(key);
    const watch = {generation, sid, socket: ws, callId: call.id, timer:null};
    taskWatches.set(key, watch);
    const ready = () => taskWatches.get(key) === watch && generation === epoch && active && sid === session &&
      watch.socket === ws && ws?.readyState === WebSocket.OPEN;
    async function tick() {
      watch.timer = null;
      if (!ready()) return;
      try {
        const response = await fetch(`/api/voice/task_status?session=${encodeURIComponent(sid)}` +
          (receipt.task_watch_id ? `&task=${encodeURIComponent(receipt.task_watch_id)}` : ''), {cache:'no-store'});
        if (!response.ok || !ready()) return;
        const result = await response.json();
        if (!ready() || !result?.status || taskRunning(result)) return;
        // Retire before sending, so duplicate receipts/ticks cannot announce twice.
        taskWatches.delete(key);
        trackDemoReadback(result);
        line('ESP32', String(result.answer || result.summary || result.reason || result.status).slice(0, 200));
        const draft = result.status === 'awaiting_confirmation'
          ? ' Read the draft readback exactly aloud, then wait for a NEW owner post confirmation. Do not publish.' : '';
        send({clientContent:{turns:[{role:'user',parts:[{text:'[TASK FINISHED] ' + JSON.stringify(compactReceipt(result)) +
          ' Tell the owner now in one or two short sentences what happened and the result. Do not call tools.' + draft}]}],turnComplete:true}});
      } catch { /* Retry status only; never repeat the device input. */ }
      finally { if (ready()) watch.timer = setTimeout(() => void tick(), TASK_INTERVAL_MS); }
    }
    watch.timer = setTimeout(() => void tick(), TASK_INTERVAL_MS);
  }
  const SCREEN_INTERVAL_MS = 1500;
  const cameraButton = document.getElementById('voice-camera-start');
  const {isStopIntent, isActionIntent} = window.PhoneVoiceIntents;
  const state = text => { el('voice-status').textContent = text; };
  const line = (role, text) => {
    if (!text) return;
    const log = el('voice-transcript');
    let row = log.lastElementChild;
    if (!row || row.dataset.role !== role) {
      row = document.createElement('p'); row.dataset.role = role;
      const b = document.createElement('b'); b.textContent = `${role}: `;
      row.append(b, document.createTextNode('')); log.append(row);
    }
    if (role === 'ESP32') { row.lastChild.textContent = text; }
    else { row.lastChild.textContent += text; }
    while (log.children.length > 16) log.firstElementChild.remove();
    log.scrollTop = log.scrollHeight;
  };
  const send = data => { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data)); };
  function clearAudio() {
    demoReadback = null; // An interrupted draft readback never permits posting.
    for (const node of playing) { try { node.stop(); } catch {} }
    playing.clear(); nextAudio = 0;
  }
  function play(data, rate = 24000) {
    if (!output || output.state === 'closed') return;
    const raw = atob(data), bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
    const pcm = new DataView(bytes.buffer), buffer = output.createBuffer(1, bytes.length / 2, rate);
    const floats = buffer.getChannelData(0);
    for (let i = 0; i < floats.length; i++) floats[i] = pcm.getInt16(i * 2, true) / 32768;
    const node = output.createBufferSource(); node.buffer = buffer; node.connect(output.destination);
    if (demoReadback) demoReadback.heard = true;
    playing.add(node); node.onended = () => { playing.delete(node); maybeFinishDemoReadback(); };
    const at = Math.max(output.currentTime + .015, nextAudio);
    node.start(at); nextAudio = at + buffer.duration;
  }
  async function jsonPost(path, body) {
    const response = await fetch(path, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }
  function trackDemoReadback(receipt) {
    if (receipt.status !== 'awaiting_confirmation' || !receipt.draft_id || !receipt.readback) return;
    demoReadback = {id: receipt.draft_id, expected: receipt.readback, text: '', heard: false, turnDone: false, session};
  }
  function maybeFinishDemoReadback() {
    const r = demoReadback;
    if (!r || !r.heard || !r.turnDone || playing.size || !r.text.includes(r.expected)) return;
    demoReadback = null;
    // Input handling waits on this receipt. Failed acknowledgment is never
    // retried, so a lost receipt cannot silently permit a publication.
    controlReady = controlReady.catch(() => {}).then(() => jsonPost('/api/voice/demo-readback',
      {session:r.session, draft_id:r.id, text:r.text.slice(0,1000)})).catch(() => {});
  }
  function flushTurns() {
    const turns = [];
    const owner = ownerTurn.trim(), milo = miloTurn.trim();
    ownerTurn = ''; miloTurn = '';
    if (owner) turns.push({role: 'owner', text: owner.slice(0, 4000)});
    if (milo) turns.push({role: 'milo', text: milo.slice(0, 4000)});
    if (!turns.length) return turnsFlushed;
    const body = JSON.stringify({kind: 'voice', turns});
    const previous = turnsFlushed;
    turnsFlushed = previous.then(() => fetch('/api/companion/conversation', {method: 'POST', keepalive: true,
      headers: {'Content-Type': 'application/json'}, body})).then(() => {}, () => {});
    return turnsFlushed;
  }
  function stopScreen() {
    clearTimeout(screenTimer); screenTimer = null;
  }
  function scheduleScreen(generation) {
    if (generation !== epoch || !active) return;
    stopScreen();
    screenTimer = setTimeout(() => void screenTick(generation), SCREEN_INTERVAL_MS);
  }
  async function screenTick(generation) {
    if (generation !== epoch || !active || !ws) return;
    const socket = ws, sid = session;
    const ready = () => generation === epoch && active &&
      socket === ws && sid === session && socket.readyState === WebSocket.OPEN &&
      !document.hidden && socket.bufferedAmount <= 262144;
    try {
      if (!ready()) return;
      const response = await fetch('/api/voice/screen/frame', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session: sid})});
      if (response.status !== 200 || !ready()) return;
      const buffer = await response.arrayBuffer();
      if (ready()) sendVideo(buffer);
    } catch { /* A missed frame must not interrupt audio or queue old frames. */ }
    finally { scheduleScreen(generation); }
  }
  function sendVideo(buffer) {
    send({realtimeInput: {video: {data: frameBase64(buffer), mimeType: 'image/jpeg'}}});
  }
  function frameBase64(buffer) {
    const bytes = new Uint8Array(buffer); let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }
  function interruptPhone(text, ids = []) {
    const controlSession = session, controlEpoch = epoch;
    stopLatched = true; controlVersion++; clearAudio();
    for (const id of ids) cancelledCalls.add(id);
    state(label('正在停止手机…', 'Stopping phone…'));
    controlReady = controlReady.catch(() => {}).then(() => jsonPost('/api/voice/interrupt', {session:controlSession, text, ids}))
      .then(receipt => {
        if (controlEpoch !== epoch) return receipt;
        if (receipt.status === 'stopped') {
          const done = receipt.stop_summary?.was_running ? ` · ${String(receipt.stop_summary.line || '').slice(0, 200)}` : '';
          line('ESP32', label('已停止，等待下一条操作指令', 'Stopped. Waiting for a new action request.') + done);
          state(label('已停止 · 麦克风仍在聆听', 'Stopped · still listening'));
        }
        return receipt;
      }).catch(() => {
        state(label('停止回执未确认，请使用红色紧急停止按钮', 'Stop receipt unavailable. Use Emergency stop.'));
      });
    return controlReady;
  }
  function handleInput(text) {
    const now = Date.now();
    if (newInput || now-lastInputAt > 1200) { inputText = ''; newInput = false; intentReported = false; ownerInputTurn++; }
    lastInputAt = now; inputVersion++; inputText += text;
    if (isStopIntent(inputText)) {
      if (!stopLatched) void interruptPhone(inputText.slice(-512));
    } else if (isActionIntent(inputText) && (!intentReported || stopLatched)) {
      intentReported = true;
      const intent = inputText, controlSession = session, controlEpoch = epoch;
      controlReady = controlReady.catch(() => {}).then(() => jsonPost('/api/voice/resume', {session:controlSession, text:intent.slice(0,512)}))
        .then(r => { if (controlEpoch === epoch && !r.paused) { stopLatched = false; state(label('聆听中', 'Listening')); } });
    }
  }
  async function tool(call, generation, sid) {
    await controlReady;
    if (cancelledCalls.has(call.id)) return;
    if (['phone_command','phone_calculate','phone_restock_note','phone_explore','companion_remember','companion_task_checkpoint','use_app_tool','start_app_goal','ipad_demo','open_camera','flip_camera','take_photo','close_camera'].includes(call.name) && stopLatched) {
      send({toolResponse:{functionResponses:[{id:call.id,name:call.name,response:{status:'stopped',terminal:true,instruction:'The previous stop is still latched; this call was not executed. Ask the user to say the action again explicitly. Do not claim you stopped an exploration in response to this call.'}}]}});
      return;
    }
    const issuedVersion = controlVersion;
    const identity = JSON.stringify([call.name, call.args || {}]);
    let entry = calls.get(call.id);
    if (entry && entry.identity !== identity) throw new Error('Conflicting tool call ID');
    if (!entry) {
      pending++;
      state(label('正在操作手机…', 'Controlling phone…'));
      const promise = jsonPost('/api/voice/tool', {session: sid, id: call.id, name: call.name, args: call.args || {}, owner_turn: ownerInputTurn, utterance: ['phone_command','phone_explore','phone_calculate','phone_restock_note','companion_remember','companion_task_checkpoint','use_app_tool','start_app_goal','add_to_current_task','open_camera','flip_camera','take_photo','close_camera','ipad_demo'].includes(call.name) ? inputText.slice(0,1000) : ''})
        .catch(() => ({status: 'receipt_unavailable', running:true, terminal:false, instruction: 'Stay silent; you will be told when it finishes. Do not retry input or call phone_status.'}))
        .finally(() => { pending--; });
      entry = {identity, promise, name:call.name, inputVersion}; calls.set(call.id, entry);
    }
    let receipt = await entry.promise;
    if (generation !== epoch || !active) return;
    if (cancelledCalls.has(call.id)) return; // Google says do not answer cancelled calls.
    // A receipt issued before Stop cannot pause a subsequently resumed user turn.
    if (receipt.paused && issuedVersion === controlVersion && !stopLatched) { stopLatched = true; controlVersion++; }
    // A real stopped receipt keeps its stop_summary; anything else issued before a stop is stale.
    if (issuedVersion !== controlVersion && receipt.status !== 'stopped') receipt = {status:'stopped',terminal:true,paused:true};
    if (generation !== epoch || !active) return;
    trackDemoReadback(receipt);
    watchTask(call, receipt, generation, sid);
    const resultMessage = receipt.status === 'photo_taken' ? label('已拍照', 'Photo taken')
      : receipt.status === 'photo_uncertain' ? label('已按快门，但未确认拍到', 'Shutter pressed, photo not confirmed')
      : receipt.status === 'photo_not_taken' ? label('未拍照', 'No photo taken')
      : receipt.status === 'camera_opened' ? label('相机已打开', 'Camera opened')
      : receipt.status === 'camera_closed' ? label('相机已关闭', 'Camera closed')
      : receipt.status === 'camera_flipped' ? label('已翻转摄像头', 'Camera flipped')
      : receipt.status === 'verified_discovery' ? label('已带回有来源核对的发现', 'Source-checked discovery ready')
      : receipt.status === 'needs_review' ? label('草稿已在本地准备好，未发布', 'Local draft prepared. Not published')
      : receipt.status === 'completed' ? label('工具执行结束，请检查实际画面', 'Tool finished. Check the live screen')
      : receipt.status === 'model_reported_done' ? label('操作完成', 'Completed')
      : receipt.status === 'failed' || receipt.status === 'idle_or_failed' ? label('操作未完成，已停止。请给出下一条指令。', 'Task failed and stopped. Give a new instruction to continue.')
      : receipt.status === 'started' ? label('已开始，正在执行…', 'Started. Working on it…')
      : receipt.status === 'note_added' ? label('已转告正在执行的任务', 'Passed to the running task')
      : receipt.status === 'note_recorded' ? label('已记下，当前步骤完成后再处理', 'Noted for after this step')
      : receipt.status === 'no_running_task' ? label('当前没有正在执行的任务', 'No task is running')
      : receipt.status === 'in_progress' || receipt.status === 'busy' ? label('正在执行，请稍候…', 'Working, please wait…')
      : receipt.status === 'not_ready' ? label('手机未就绪，请检查投屏和连接', 'Phone not ready. Check mirroring and connection.')
      : receipt.status === 'receipt_unavailable' ? label('操作回执暂不可用，请勿重复输入', 'Receipt unavailable. Do not repeat typing.')
      : receipt.status === 'stopped' ? label('已停止', 'Stopped') : label('等待新指令', 'Waiting for a new instruction');
    // Realtime detail: the latest step while running, or what was done after a stop.
    const detail = receipt.running && receipt.progress?.line ? receipt.progress.line
      : receipt.status === 'stopped' && receipt.stop_summary?.was_running ? receipt.stop_summary.line : '';
    const shown = detail ? `${resultMessage} · ${String(detail).slice(0, 200)}` : resultMessage;
    const summaryKey = JSON.stringify([receipt.task_id, receipt.goal, receipt.status, shown]);
    if (summaryKey !== lastReceiptSummary) { line('ESP32', shown); lastReceiptSummary = summaryKey; }
    if (call.name === 'phone_status' && taskWatches.size && !taskRunning(receipt)) {
      receipt = {...receipt, instruction:'Stay silent; the browser will deliver [TASK FINISHED] with the result. Do not call tools.'};
    }
    send({toolResponse: {functionResponses: [{id: call.id, name: call.name, response: receipt}]}});
    state(stopLatched ? label('已停止 · 麦克风仍在聆听', 'Stopped · still listening') : receipt.status === 'not_ready' ? (receipt.reason || label('设备未就绪，请查看连接状态', 'Device not ready. Check connection status')) : label('聆听中', 'Listening'));
  }
  async function receive(event, generation, sid) {
    if (generation !== epoch) return;
    const data = JSON.parse(typeof event.data === 'string' ? event.data : event.data instanceof ArrayBuffer ? new TextDecoder().decode(event.data) : await event.data.text());
    if (generation !== epoch) return;
    if (data.setupComplete) {
      clearTimeout(setupTimer);
      active = true;
      state(label('聆听中', 'Listening'));
      el('voice-mute').disabled = false;
      if (cameraButton) cameraButton.disabled = false;
      scheduleScreen(generation);
    }
    if (data.error) throw new Error(data.error.message || 'Gemini Live error');
    if (data.goAway) {
      await end(label('语音会话即将到期，请重新开始；不会重放操作', 'Session expiring. Reconnect explicitly; no commands are replayed.'));
      return;
    }
    const c = data.serverContent;
    if (c) {
      if (c.interrupted) clearAudio();
      if (c.inputTranscription?.text) { ownerTurn += c.inputTranscription.text; line('You', c.inputTranscription.text); handleInput(c.inputTranscription.text); }
      if (c.outputTranscription?.text) { if (demoReadback) demoReadback.text += c.outputTranscription.text; miloTurn += c.outputTranscription.text; newInput = true; line('Gemini', c.outputTranscription.text); }
      if (c.turnComplete) {
        newInput = true; void flushTurns();
      }
      for (const p of c.modelTurn?.parts || []) {
        if (p.inlineData?.data) play(p.inlineData.data, Number(p.inlineData.mimeType?.match(/rate=(\d+)/)?.[1] || 24000));
      }
      if (c.turnComplete && demoReadback) { demoReadback.turnDone = true; maybeFinishDemoReadback(); }
    }
    for (const call of data.toolCall?.functionCalls || []) {
      // Never block receiving audio or a spoken stop while another tool is pending.
      void tool(call, generation, sid).catch(error => state(error.message));
    }
    if (data.toolCallCancellation) {
      const ids = data.toolCallCancellation.ids || [];
      const hasPhoneCommand = ids.some(id => ['phone_command','phone_calculate','phone_restock_note','phone_explore','companion_remember','use_app_tool','start_app_goal','ipad_demo','open_camera','flip_camera','take_photo','close_camera'].includes(calls.get(id)?.name));
      for (const id of ids) cancelledCalls.add(id);
      if (hasPhoneCommand) {
        const cancellationEpoch = epoch, cancellationSession = session;
        const previousInputVersion = Math.max(...ids.map(id => calls.get(id)?.inputVersion ?? inputVersion));
        controlReady = interruptPhone('', ids).then(() => {
          if (cancellationEpoch !== epoch) return;
          // A new navigation request can itself barge in. The old tool is
          // cancelled first; only a new recognized action may release the latch.
          if (inputVersion > previousInputVersion && !isStopIntent(inputText) && isActionIntent(inputText)) {
            return jsonPost('/api/voice/resume', {session:cancellationSession, text:inputText.slice(0,512)})
              .then(r => { if (cancellationEpoch === epoch && !r.paused) stopLatched = false; });
          }
        });
      }
    }
  }
  async function end(message) {
    void flushTurns();
    stopScreen(); stopTaskWatch();
    ++epoch; active = false; document.body.dataset.voiceActive = "false";
    clearTimeout(timer); clearTimeout(setupTimer);
    const socket = ws; ws = null;
    if (socket) { socket.onclose = null; socket.close(); }
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    processor?.disconnect(); processor = null; source?.disconnect(); source = null;
    clearAudio();
    if (mic) { void mic.close().catch(() => {}); mic = null; }
    if (output) { void output.close().catch(() => {}); output = null; }
    if (session) { void fetch('/api/voice/end', {method: 'POST', keepalive: true, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session})}).catch(() => {}); }
    session = null;
    el('voice-start').disabled = false; el('voice-end').hidden = true;
    if (cameraButton) cameraButton.disabled = false;
    el('voice-mute').hidden = true;
    state(message || label('语音已结束', 'Voice ended'));
  }
  async function start() {
    if (el('voice-start').disabled) return;
    stopScreen(); stopTaskWatch(); watchedCalls.clear(); watchedTasks.clear();
    const generation = ++epoch; document.body.dataset.voiceActive = "true";
    el('voice-start').disabled = true; el('voice-end').hidden = false;
    if (cameraButton) cameraButton.disabled = true;
    state(label('连接 Gemini 3.8 Live…', 'Connecting Gemini 3.8 Live…'));
    calls.clear(); cancelledCalls.clear(); lastReceiptSummary = ""; muted = false; stopLatched = false;
    controlVersion = 0; inputText = ""; newInput = true; intentReported = false; controlReady = Promise.resolve(); ownerInputTurn = 0; demoReadback = null;
    const fail = message => { if (generation === epoch) return end(message); };
    timer = setTimeout(() => void fail(label('麦克风或连接未响应，请在 Chrome 打开此页面并允许麦克风', 'Microphone or connection timed out. Open this page in Chrome and allow microphone access.')), 30000);
    try {
      // Create/resume playback during the actual click gesture, before async auth.
      output = new AudioContext({sampleRate: 24000}); await output.resume();
      const acquired = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      if (generation !== epoch) { acquired.getTracks().forEach(t => t.stop()); return; }
      stream = acquired;
      // Finish saving previous turns before starting a fresh conversation (bounded wait).
      await Promise.race([turnsFlushed, new Promise(resolve => setTimeout(resolve, CONVERSATION_FLUSH_WAIT_MS))]);
      if (generation !== epoch) return;
      const credentials = await jsonPost('/api/voice/session', {});
      if (generation !== epoch) return;
      session = credentials.session;
      mic = new AudioContext({sampleRate: 16000}); await mic.resume();
      await mic.audioWorklet.addModule('/static/voice-worklet.js');
      if (generation !== epoch) return;
      if (mic.sampleRate !== 16000) throw new Error('Microphone resampling to 16 kHz is unavailable');
      processor = new AudioWorkletNode(mic, 'phone-mic');
      source = mic.createMediaStreamSource(stream); source.connect(processor);
      const silent = mic.createGain(); silent.gain.value = 0; processor.connect(silent); silent.connect(mic.destination);
      const socket = new WebSocket('wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained?access_token=' + encodeURIComponent(credentials.token));
      ws = socket; socket.binaryType = "arraybuffer";
      socket.onopen = () => { if (generation === epoch) send({setup: credentials.setup}); };
      socket.onmessage = e => { void receive(e, generation, credentials.session).catch(error => void fail(error.message)); };
      socket.onerror = () => void fail(label('Gemini 连接失败，请重试', 'Gemini connection failed. Try again.'));
      socket.onclose = e => void fail(`Gemini disconnected (${e.code}). ${e.reason || 'Start voice again; no automatic replay.'}`);
      setupTimer = setTimeout(() => { if (!active) void fail(label('Gemini 连接超时，请重试', 'Gemini setup timed out. Try again.')); }, 15000);
      processor.port.onmessage = ({data}) => {
        if (!active || muted || generation !== epoch || socket.bufferedAmount > 65536) return;
        const bytes = new Uint8Array(data.length * 2), view = new DataView(bytes.buffer);
        data.forEach((v, i) => view.setInt16(i*2, Math.max(-32768, Math.min(32767, Math.round(v*32768))), true));
        let binary = ''; for (const b of bytes) binary += String.fromCharCode(b);
        send({realtimeInput: {audio: {data: btoa(binary), mimeType: 'audio/pcm;rate=16000'}}});
      };
      el('voice-mute').hidden = false; el('voice-mute').disabled = true;
      el('voice-mute').textContent = label('静音', 'Mute');
      clearTimeout(timer);
      timer = setTimeout(() => void end(label('已达到 14 分钟会话上限，请重新开始', '14-minute session limit reached. Start again.')), 14*60*1000);
    } catch (error) { await fail(error.message); }
  }
  el('voice-start').addEventListener('click', start);
  cameraButton?.addEventListener('click', () => {
    if (!active || cameraButton.disabled) return;
    // A user request to the current conversation; no new token, socket or reconnect.
    send({clientContent: {turns: [{role: 'user', parts: [{text: 'Open the camera.'}]}], turnComplete: true}});
    newInput = true; handleInput('Open the camera.');
    ownerTurn += 'Open the camera.'; line('You', label('打开相机。', 'Open the camera.'));
  });
  el('voice-end').addEventListener('click', () => void end());
  el('voice-mute').addEventListener('click', () => {
    muted = !muted;
    stream?.getAudioTracks().forEach(track => { track.enabled = !muted; });
    if (muted) send({realtimeInput: {audioStreamEnd: true}});
    el('voice-mute').textContent = muted ? label('取消静音', 'Unmute') : label('静音', 'Mute');
    state(muted ? label('麦克风已静音', 'Microphone muted') : label('聆听中', 'Listening'));
  });
  el('estop').addEventListener('click', () => { if (ws) void end(label('紧急停止：语音已结束', 'Emergency stop: voice ended')); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && ws && !el('zoom').open) void interruptPhone('stop'); });
  window.addEventListener('pagehide', () => { void end(); });
  fetch('/api/voice/status').then(r => r.json()).then(s => {
    if (cameraButton) { cameraButton.hidden = s.camera_controls !== true; cameraButton.disabled = !active; }
    state(s.configured ? label('就绪 · Gemini 3.8 Live', 'Ready · Gemini 3.8 Live') : label('未配置 Gemini Key', 'Gemini key not configured'));
  }).catch(() => state(label('语音服务不可用', 'Voice service unavailable')));
})();
