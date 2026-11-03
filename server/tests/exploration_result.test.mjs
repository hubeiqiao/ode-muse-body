import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

test('Ode displays native source identity and visible evidence without inventing a URL', async () => {
  const {explorationResultText} = await import('../../companion-ui/src/explorationResult.js');
  const text = explorationResultText({state:'completed', evidence:[{claim:'A checked finding',quote:'Actual pixels',source:'X · @example_owner',url:''}]});
  assert.match(text, /Source: X · @example_owner/);
  assert.match(text, /Actual pixels/);
  assert.doesNotMatch(text, /undefined/);
  assert.match(explorationResultText({state:'failed', reason:'X search suggestions were visible; no post could be verified.'}), /X search suggestions/);
});

test('all spoken action requests reach the resume gate, and original phone words reach the server', async () => {
  const {default: intents} = await import('../hidagent/static/voice-intents.js');
  for (const words of ['Surprise me','Whatever you like','Do something by yourself','Find posts on X about IELTS speaking'])
    assert.equal(intents.isActionIntent(words),true,words);
  const voice = readFileSync(new URL('../hidagent/static/voice.js', import.meta.url), 'utf8');
  assert.match(voice, /utterance: \['phone_command','phone_explore'/);
});
