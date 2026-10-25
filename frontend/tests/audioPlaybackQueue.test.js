import assert from 'node:assert/strict';
import test from 'node:test';
import { AudioPlaybackQueue } from '../src/audioPlaybackQueue.js';

function createAudioContext(currentTime = 0) {
  const sources = [];
  return {
    currentTime,
    destination: {},
    sources,
    createBufferSource() {
      const source = {
        startTime: null,
        stopped: false,
        disconnected: false,
        onended: null,
        connect() {},
        start(time) {
          this.startTime = time;
          sources.push(this);
        },
        stop() {
          this.stopped = true;
        },
        disconnect() {
          this.disconnected = true;
        },
      };
      return source;
    },
  };
}

test('schedules audio buffers in arrival order without gaps', () => {
  const queue = new AudioPlaybackQueue();
  const audioContext = createAudioContext(4);
  const onDrained = () => {};

  queue.schedule(audioContext, { duration: 0.2 }, onDrained);
  queue.schedule(audioContext, { duration: 0.3 }, onDrained);

  assert.deepEqual(audioContext.sources.map((source) => source.startTime), [4, 4.2]);
  assert.equal(queue.sources.size, 2);
});

test('clears scheduled audio and resets the timeline for a new turn', () => {
  const queue = new AudioPlaybackQueue();
  const audioContext = createAudioContext(2);

  queue.schedule(audioContext, { duration: 1 }, () => {
    assert.fail('Clearing an interrupted source must not report normal completion.');
  });
  const [source] = audioContext.sources;
  queue.clear(audioContext.currentTime);

  assert.equal(source.stopped, true);
  assert.equal(source.disconnected, true);
  assert.equal(queue.sources.size, 0);
  assert.equal(queue.nextPlaybackTime, 2);

  audioContext.currentTime = 2.1;
  queue.schedule(audioContext, { duration: 0.1 }, () => {});
  assert.equal(audioContext.sources[1].startTime, 2.1);
});

test('schedules late audio immediately instead of behind an expired queue', () => {
  const queue = new AudioPlaybackQueue();
  const audioContext = createAudioContext(0);

  queue.schedule(audioContext, { duration: 0.1 }, () => {});
  audioContext.currentTime = 0.25;
  queue.schedule(audioContext, { duration: 0.1 }, () => {});

  assert.equal(audioContext.sources[1].startTime, 0.25);
});

test('removes completed sources and reports when playback drains', () => {
  const queue = new AudioPlaybackQueue();
  const audioContext = createAudioContext(0);
  let remainingSources = null;

  queue.schedule(audioContext, { duration: 0.1 }, (remaining) => {
    remainingSources = remaining;
  });
  const [source] = audioContext.sources;
  source.onended();

  assert.equal(queue.sources.size, 0);
  assert.equal(source.disconnected, true);
  assert.equal(remainingSources, 0);
});
