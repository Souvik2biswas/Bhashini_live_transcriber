/**
 * AudioWorkletProcessor for Bhashini Live Transcriber
 * ──────────────────────────────────────────────────
 * Runs on browser's dedicated audio render thread.
 * Captures, resamples (to 16kHz mono), and converts Float32 audio to 16-bit PCM.
 * Posts finished buffers back to the main thread without blocking the UI.
 */
class AudioProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.inputSampleRate = sampleRate; // Global sampleRate from context
        this.targetSampleRate = 16000;
        this.lastSampleOffset = 0.0;
        this.bufferSize = 1600; // 100ms at 16kHz
        this.buffer = new Int16Array(this.bufferSize);
        this.bufferWriteIndex = 0;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || !input[0] || input[0].length === 0) {
            return true;
        }

        const inputChannel = input[0];
        const ratio = this.inputSampleRate / this.targetSampleRate;
        
        let index = this.lastSampleOffset;
        
        // Perform linear interpolation downsampling
        while (index < inputChannel.length) {
            const idx = Math.floor(index);
            const nextIdx = idx + 1 < inputChannel.length ? idx + 1 : idx;
            const weight = index - idx;
            const interpolated = inputChannel[idx] * (1 - weight) + inputChannel[nextIdx] * weight;
            
            // Convert Float32 (-1.0 to 1.0) to Int16 PCM
            const s = Math.max(-1.0, Math.min(1.0, interpolated));
            const pcmSample = s < 0 ? s * 0x8000 : s * 0x7FFF;
            
            this.buffer[this.bufferWriteIndex++] = pcmSample;
            
            if (this.bufferWriteIndex >= this.bufferSize) {
                // Send the raw ArrayBuffer back as a transferable object to avoid copying overhead
                this.port.postMessage(this.buffer.buffer, [this.buffer.buffer]);
                // Re-allocate buffer since the previous one was transferred
                this.buffer = new Int16Array(this.bufferSize);
                this.bufferWriteIndex = 0;
            }
            
            index += ratio;
        }
        this.lastSampleOffset = index - inputChannel.length;

        return true;
    }
}

registerProcessor('audio-processor', AudioProcessor);
