export function explorationResultText(state) {
  const finding = state.state === 'completed' && state.evidence?.[0];
  if (finding) return finding.claim + '\n\nSource: ' + (finding.source || finding.url)
    + '\nVisible quote: ' + finding.quote;
  if (state.state === 'stopped') return 'Stopped. I’ll wait for your next instruction.';
  return 'I could not verify a discovery this time. ' + (state.reason || 'No automatic retry was made.');
}
