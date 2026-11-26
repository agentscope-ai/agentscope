// Run with Node.js 22.13+: node --test tests/wav_playback_test.mjs
// Exercise the actual TypeScript players without a browser or frontend build.
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const root = new URL('../examples/web_ui/frontend/src/', import.meta.url);
let utils = readFileSync(new URL('utils/streamingAudio.ts', root), 'utf8');
utils = utils.slice(utils.indexOf('type Bytes ='), utils.indexOf('interface Session {'));
const pipeline = readFileSync(new URL('lib/pipelineAudioPlayer.ts', root), 'utf8')
  .replace(/^import [^\n]*\n/gm, '');
const source = stripTypeScriptTypes(utils + '\n' + pipeline, { mode: 'strip' })
  .replace(/\bexport /g, '');
const { WavStreamPlayer, PipelineAudioPlayer } = new Function(
  source + '\nreturn { WavStreamPlayer, PipelineAudioPlayer };',
)();

let frames;
class FakeAudioContext {
  state = 'running';
  currentTime = 0;
  destination = {};
  createBuffer(_channels, count, rate) {
    return { duration: count / rate, getChannelData: () => new Float32Array(count) };
  }
  createBufferSource() {
    return {
      connect() {}, disconnect() {}, stop() {},
      start() { frames += this.buffer.duration * 8000; },
    };
  }
  close() { return Promise.resolve(); }
}
globalThis.AudioContext = FakeAudioContext;

function wav(size) {
  const bytes = Buffer.alloc(58);
  bytes.write('RIFF', 0); bytes.writeUInt32LE(50, 4); bytes.write('WAVEfmt ', 8);
  bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20);
  bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(8000, 24);
  bytes.writeUInt32LE(16000, 28); bytes.writeUInt16LE(2, 32);
  bytes.writeUInt16LE(16, 34); bytes.write('data', 36);
  bytes.writeUInt32LE(size, 40); bytes.writeInt16LE(1000, 44);
  bytes.write('LIST', 46); bytes.writeUInt32LE(4, 50); bytes.write('INFO', 54);
  return bytes;
}

for (const kind of ['stream', 'pipeline']) {
  for (const split of [false, true]) {
    for (const unknown of [false, true]) {
      test(`${kind} ${split ? 'split' : 'complete'} ${unknown ? 'unknown' : 'bounded'} WAV`, () => {
        frames = 0;
        const bytes = wav(unknown ? 0xffffffff : 2);
        const parts = split ? [bytes.subarray(0, 45), bytes.subarray(45, 50), bytes.subarray(50)] : [bytes];
        if (kind === 'stream') {
          const player = new WavStreamPlayer();
          for (const part of parts) player.append(part);
          player.dispose();
        } else {
          const player = new PipelineAudioPlayer(new FakeAudioContext(), () => {}, error => { throw error; });
          player.start('x', 'audio/wav');
          for (const part of parts) player.append('x', part.toString('base64'));
          player.stop();
        }
        assert.equal(frames, unknown ? 7 : 1);
      });
    }
  }
}
