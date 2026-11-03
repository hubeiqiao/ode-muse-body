export function mirrorMessage(status) {
  if (!status) return 'Checking the screen connection.';
  if (status.screen) return 'Screen connected.';
  const state = status.mirror_diagnostics?.state;
  if (state === 'source_disconnected') return 'AirPlay session disconnected. Waiting for the iPad to send a new screen.';
  if (state === 'waiting_for_first_frame') return 'AirPlay session detected. Waiting for its first new frame.';
  if (state === 'receiver_heartbeat_lost' || state === 'receiver_disconnected') return 'Reconnecting the local screen stream. AirPlay is not being restarted.';
  if (state === 'receiver_waiting') return 'The screen receiver is waiting for its video process.';
  return 'No fresh mirrored frame. The iPad screen connection is not confirmed.';
}
export function mirrorAge(status) {
  const age=status?.mirror_diagnostics?.frame_age_s;
  if (typeof age !== 'number' || !Number.isFinite(age) || age < 0) return 'No frame received.';
  return `Last frame received ${Math.floor(age)}s ago.`;
}
export function modelRoute(status) {
  const m=status?.models;
  return m ? `Control: ${m.judge || 'unknown'} / ${m.backup || 'no backup'} → ESP32` : 'Checking control models';
}
