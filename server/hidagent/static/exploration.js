/* Explicit public exploration only. Polling and rendering never submit work. */
(function (root) {
  "use strict";
  function createController(send, changed, newId) {
    let status = null, request = null, pending = false, uncertain = false, stopping = false, error = "";
    const busy = () => pending || uncertain || stopping || !!status?.exploration?.busy;
    const view = () => ({ status, pending, uncertain, stopping, error, busy: busy() });
    const notify = () => changed(view());
    return {
      busy,
      update(next) {
        status = next;
        if (request && next.exploration?.request_id === request) { pending = uncertain = false; if (error === "unknown") error = ""; }
        notify();
      },
      async start(goal) {
        goal = goal.trim();
        if (!goal || goal.length > 1000 || busy() || status?.agent?.running || !status?.exploration?.available ||
            !status.device || !status.screen || !status.calibrated) return false;
        request = newId(); pending = true; error = ""; notify();
        try {
          const result = await send("/api/exploration/start", { request_id: request, goal });
          status = { ...status, exploration: result }; pending = false;
        } catch (e) {
          pending = false;
          if (e.definite) { request = null; error = "rejected"; }
          else { uncertain = true; error = "unknown"; }
        }
        notify(); return true;
      },
      async stop() {
        const round = status?.exploration;
        if (!round?.round_id || !round.busy || stopping) return false;
        stopping = true; error = ""; notify();
        try {
          const result = await send("/api/exploration/stop", { round_id: round.round_id });
          // A late stop receipt for an old round cannot replace a newer status.
          if (status?.exploration?.round_id === round.round_id) status = { ...status, exploration: result };
        } catch { error = "stopUnknown"; }
        stopping = false; notify(); return true;
      }
    };
  }
  if (typeof module !== "undefined") module.exports = { createController };
  if (!root.document) return;
  const $ = (id) => document.getElementById(id);
  if (!$("exploration-panel")) return;
  const copy = {
    en: { title:"Give AI a phone · prototype", hint:"Start one public discovery round. The device executor opens a public source and checks its visible URL or app and author/title. Up to 90 seconds; no background browsing. You can also start voice and ask it to explore.", goal:"What would you like it to investigate?", start:"Start exploring", stop:"Stop this round", disabled:"Public browsing is not enabled on this service. No exploration was started.", unready:"Waiting for a live, calibrated device.", voice:"Voice is connected. Say “explore a topic yourself”; this button is for typed requests.", idle:"Waiting for you to start.", running:"Exploring", completed:"Source-checked finding", failed:"No verified discovery returned", stopped:"Stopped", expired:"Time limit reached", proposal:"Investigation proposal, not yet verified", source:"Source", quote:"Visible passage", rejected:"The service rejected this request. Nothing was automatically retried.", unknown:"Start delivery is uncertain. Waiting for status; do not submit again.", stopUnknown:"Stop receipt is uncertain. Check status or use Emergency stop.", waiting:"Waiting for current work to finish", release:"Input release is unconfirmed; the device remains occupied." },
    zh: { title:"给AI一台手机 · 原型", hint:"由你启动一次公开探索。执行器会打开公开来源，核对画面中的网址或应用及作者/标题。最多90秒，不会后台浏览。也可以开始语音后直接说“自己选题，带回一个发现”。", goal:"你想让它调查什么？", start:"开始探索", stop:"停止本回合", disabled:"此服务尚未启用公开浏览，没有启动探索。", unready:"等待真实画面与当前设备校准就绪。", voice:"语音已连接，请直接说“自己选题，带回一个发现”；此按钮用于文字启动。", idle:"等待你开始。", running:"正在探索", completed:"有来源核对的发现", failed:"未带回已核实发现", stopped:"已停止", expired:"已到时间上限", proposal:"调查提案，尚未核实", source:"来源", quote:"画面中的原文", rejected:"服务拒绝了本次请求，没有自动重试。", unknown:"开始请求投递不确定，等待状态确认，请勿重复提交。", stopUnknown:"停止回执不确定，请检查状态或使用紧急停止。", waiting:"等待当前任务结束", release:"输入释放尚未确认，设备继续保持占用。" }
  };
  let locale = "en";
  const voiceActive = () => document.body.dataset.voiceActive === "true";
  function render(v) {
    const c = copy[locale], s = v.status, e = s?.exploration;
    $("exploration-panel").hidden = !e;
    $("exploration-title").textContent = c.title; $("exploration-hint").textContent = c.hint;
    $("exploration-goal-label").textContent = c.goal;
    $("exploration-start").textContent = c.start; $("exploration-stop").textContent = c.stop;
    $("exploration-start").disabled = !e?.available || v.busy || !!s?.agent?.running || !s?.device || !s?.screen || !s?.calibrated || voiceActive();
    $("exploration-stop").disabled = !e?.busy || !e?.round_id || v.stopping;
    $("exploration-stop").hidden = !e?.busy;
    $("exploration-goal").disabled = v.busy;
    let message = !e?.available ? c.disabled : !s.device || !s.screen || !s.calibrated ? c.unready : voiceActive() ? c.voice : s.agent?.running ? c.waiting : c[e.state] || c.idle;
    if (e?.round_id) message += ` · ${e.elapsed_s ?? 0}s · ${e.actions_used ?? 0}/8`;
    if (e?.reason && !e.busy) message += ` · ${e.reason}`;
    if (e?.release_pending) message += ` · ${c.release}`;
    if (v.error) message = c[v.error];
    $("exploration-status").textContent = message;
    $("exploration-proposal").textContent = e?.proposal ? `${c.proposal}: ${e.proposal.topic}. ${e.proposal.reason}` : "";
    const result = $("exploration-results"); result.replaceChildren();
    if (e?.state === "completed" && !e.busy) for (const item of e.evidence || []) {
      for (const text of [item.claim, `${c.quote}: ${item.quote}`, `${c.source}: ${item.source || item.url}`, item.verifier]) {
        const p = document.createElement("p"); p.textContent = text || ""; result.append(p);
      }
    }
  }
  const controller = createController(async (path, body) => {
    const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    if (!response.ok) { const e = new Error("Rejected"); e.definite = response.status >= 400 && response.status < 500; throw e; }
    return await response.json();
  }, render, () => crypto.randomUUID());
  $("exploration-start").addEventListener("click", () => { if (!voiceActive()) controller.start($("exploration-goal").value); });
  $("exploration-stop").addEventListener("click", () => controller.stop());
  root.explorationPanel = {busy:controller.busy, update(status, language) { locale = language === "zh" ? "zh" : "en"; controller.update(status); }};
})(typeof window !== "undefined" ? window : globalThis);
