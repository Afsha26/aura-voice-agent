export class AudioPlaybackQueue {
  constructor() {
    this.sources = new Set();
    this.nextPlaybackTime = 0;
  }

  schedule(audioContext, audioBuffer, onDrained) {
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);

    const startAt = Math.max(audioContext.currentTime, this.nextPlaybackTime);
    this.nextPlaybackTime = startAt + audioBuffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
      onDrained(this.sources.size);
    };
    source.start(startAt);
    return startAt;
  }

  clear(currentTime = 0) {
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // A source may have ended before the queue was cleared.
      }
      source.disconnect();
    }
    this.sources.clear();
    this.nextPlaybackTime = currentTime;
  }
}
