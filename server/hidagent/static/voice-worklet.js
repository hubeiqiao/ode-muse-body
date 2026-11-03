// Bounded 32ms packets; PCM is resampled by AudioContext, not by dropping samples.
class PhoneMic extends AudioWorkletProcessor {
  constructor() { super(); this.chunk = new Float32Array(512); this.offset = 0; }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) for (const sample of input) {
      this.chunk[this.offset++] = sample;
      if (this.offset === this.chunk.length) {
        this.port.postMessage(this.chunk); this.chunk = new Float32Array(512); this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('phone-mic', PhoneMic);
