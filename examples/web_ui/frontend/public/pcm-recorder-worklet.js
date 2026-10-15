class PCMRecorderProcessor extends AudioWorkletProcessor {
	constructor() {
		super();
		this.buffer = new Int16Array(1024);
		this.offset = 0;
		this.port.onmessage = (event) => {
			if (event.data?.type !== 'flush') return;
			this.flush();
			this.port.postMessage({ type: 'flushed' });
		};
	}

	flush() {
		if (this.offset === 0) return;
		const tail = this.buffer.slice(0, this.offset);
		this.port.postMessage(tail.buffer, [tail.buffer]);
		this.buffer = new Int16Array(1024);
		this.offset = 0;
	}

	process(inputs) {
		const input = inputs[0]?.[0];
		if (!input || input.length === 0) return true;

		for (let index = 0; index < input.length; index += 1) {
			const sample = Math.max(-1, Math.min(1, input[index]));
			this.buffer[this.offset] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
			this.offset += 1;
			if (this.offset === this.buffer.length) {
				this.flush();
			}
		}
		return true;
	}
}

registerProcessor('pcm-recorder', PCMRecorderProcessor);
