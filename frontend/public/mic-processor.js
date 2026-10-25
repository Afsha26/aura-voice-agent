class AuraMicrophoneProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pendingSamples = new Float32Array(0);
    this.resamplePosition = 0;
    this.outputSamples = [];
  }

  process(inputs) {
    const channels = inputs[0];
    if (!channels || channels.length === 0 || channels[0].length === 0) {
      return true;
    }

    const frameLength = channels[0].length;
    const monoFrame = new Float32Array(frameLength);
    for (const channel of channels) {
      for (let index = 0; index < frameLength; index += 1) {
        monoFrame[index] += channel[index] / channels.length;
      }
    }

    const merged = new Float32Array(this.pendingSamples.length + frameLength);
    merged.set(this.pendingSamples);
    merged.set(monoFrame, this.pendingSamples.length);

    const inputRatePerOutputSample = sampleRate / 16000;
    while (this.resamplePosition < merged.length - 1) {
      const lowerIndex = Math.floor(this.resamplePosition);
      const fraction = this.resamplePosition - lowerIndex;
      const sample = merged[lowerIndex]
        + (merged[lowerIndex + 1] - merged[lowerIndex]) * fraction;
      this.outputSamples.push(sample);
      this.resamplePosition += inputRatePerOutputSample;
    }

    const consumedSamples = Math.min(
      Math.max(0, Math.floor(this.resamplePosition)),
      merged.length - 1,
    );
    this.resamplePosition -= consumedSamples;
    this.pendingSamples = merged.slice(consumedSamples);

    while (this.outputSamples.length >= 1280) {
      const floatSamples = this.outputSamples.splice(0, 1280);
      const pcm = new Int16Array(floatSamples.length);
      let sumSquares = 0;
      for (let index = 0; index < floatSamples.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, floatSamples[index]));
        sumSquares += sample * sample;
        pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
      }
      const rms = Math.sqrt(sumSquares / floatSamples.length);
      this.port.postMessage(
        { pcm: pcm.buffer, speech: rms > 0.012 },
        [pcm.buffer],
      );
    }

    return true;
  }
}

registerProcessor('aura-microphone-processor', AuraMicrophoneProcessor);
