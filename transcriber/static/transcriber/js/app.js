/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * Bhashini Live Transcriber — Frontend Application
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Handles:
 *  - WebSocket connection to Django Channels
 *  - Browser microphone capture via MediaRecorder API
 *  - Real-time audio level visualization via Web Audio API
 *  - UI state management (language selection, recording, results)
 *  - Text translation mode
 */

(function () {
    'use strict';

    // ─── Configuration ──────────────────────────────────────────────────────────
    const CHUNK_INTERVAL_MS = 4000;      // Send audio every 4 seconds
    const RECONNECT_BASE_MS = 1000;      // Initial reconnect delay
    const RECONNECT_MAX_MS  = 16000;     // Max reconnect delay
    const LEVEL_UPDATE_MS   = 50;        // Audio level refresh rate

    // ─── State ──────────────────────────────────────────────────────────────────
    let ws = null;
    let mediaRecorder = null;
    let scriptProcessor = null;
    let audioStream = null;
    let audioContext = null;
    let analyser = null;
    let levelAnimFrame = null;
    let reconnectAttempts = 0;
    let isRecording = false;
    let selectedLang = 'ta';
    let chunkInterval = null;

    // Stats
    let stats = {
        chunks: 0,
        skipped: 0,
        errors: 0,
        lastLatency: null,
    };

    // ─── DOM References ─────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);

    const dom = {
        connectionBadge: $('connectionBadge'),
        badgeText:       $('connectionBadge')?.querySelector('.badge-text'),
        languageGrid:    $('languageGrid'),
        recordBtn:       $('recordBtn'),
        recordBtnIcon:   $('recordBtnIcon'),
        recordBtnLabel:  $('recordBtnLabel'),
        levelContainer:  $('levelMeterContainer'),
        levelFill:       $('levelFill'),
        levelLabel:      $('levelLabel'),
        statsBar:        $('statsBar'),
        statChunks:      $('statChunks'),
        statSkipped:     $('statSkipped'),
        statLatency:     $('statLatency'),
        statErrors:      $('statErrors'),
        resultsContainer:$('resultsContainer'),
        resultsEmpty:    $('resultsEmpty'),
        clearBtn:        $('clearBtn'),
        textInput:       $('textInput'),
        translateBtn:    $('translateBtn'),
        textOutput:      $('textOutput'),
    };


    // ═══════════════════════════════════════════════════════════════════════════
    // WebSocket
    // ═══════════════════════════════════════════════════════════════════════════

    function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/transcribe/`;

        updateConnectionStatus('connecting');

        ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            console.log('[WS] Connected');
            reconnectAttempts = 0;
            updateConnectionStatus('connected');
            dom.recordBtn.disabled = false;

            // Send language config
            sendConfig(selectedLang);
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleServerMessage(msg);
            } catch (e) {
                console.error('[WS] Failed to parse message:', e);
            }
        };

        ws.onclose = (event) => {
            console.log(`[WS] Disconnected (code=${event.code})`);
            updateConnectionStatus('disconnected');
            dom.recordBtn.disabled = true;

            if (isRecording) {
                stopRecording();
            }

            // Reconnect with exponential backoff
            const delay = Math.min(
                RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts),
                RECONNECT_MAX_MS
            );
            reconnectAttempts++;
            console.log(`[WS] Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);
            setTimeout(connectWebSocket, delay);
        };

        ws.onerror = (error) => {
            console.error('[WS] Error:', error);
            updateConnectionStatus('error');
        };
    }

    function sendConfig(lang) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'config', lang: lang }));
        }
    }

    function updateConnectionStatus(status) {
        const badge = dom.connectionBadge;
        badge.className = 'connection-badge';

        switch (status) {
            case 'connecting':
                dom.badgeText.textContent = 'Connecting…';
                break;
            case 'connected':
                badge.classList.add('connected');
                dom.badgeText.textContent = 'Connected';
                break;
            case 'disconnected':
                dom.badgeText.textContent = 'Reconnecting…';
                break;
            case 'error':
                badge.classList.add('error');
                dom.badgeText.textContent = 'Error';
                break;
        }
    }


    // ═══════════════════════════════════════════════════════════════════════════
    // Server Message Handler
    // ═══════════════════════════════════════════════════════════════════════════

    function handleServerMessage(msg) {
        switch (msg.type) {
            case 'languages':
                // Initial language list — we already have them in HTML
                break;

            case 'config_ready':
                console.log(`[Config] ${msg.lang} ready — ASR: ${msg.asr_service}, NMT: ${msg.nmt_service}`);
                break;

            case 'processing':
                removeProcessingCards();
                addProcessingCard(msg.chunk_num);
                break;

            case 'transcription':
                removeProcessingCards();
                addTranscriptionCard(msg);
                stats.chunks++;
                stats.lastLatency = msg.latency;
                updateStats();
                break;

            case 'translation':
                showTextTranslation(msg);
                break;

            case 'silence':
                stats.skipped++;
                updateStats();
                break;

            case 'no_speech':
                removeProcessingCards();
                break;

            case 'error':
                removeProcessingCards();
                addErrorCard(msg.message, msg.chunk_num);
                stats.errors++;
                updateStats();
                break;

            default:
                console.warn('[WS] Unknown message type:', msg.type);
        }
    }


    // ═══════════════════════════════════════════════════════════════════════════
    // Audio Recording
    // ═══════════════════════════════════════════════════════════════════════════

    function resample(buffer, fromSampleRate, toSampleRate) {
        if (fromSampleRate === toSampleRate) {
            return buffer;
        }
        const ratio = fromSampleRate / toSampleRate;
        const newLength = Math.round(buffer.length / ratio);
        const result = new Float32Array(newLength);
        let offsetResult = 0;
        let offsetBuffer = 0;
        while (offsetResult < result.length) {
            const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
            let accum = 0, count = 0;
            for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
                accum += buffer[i];
                count++;
            }
            result[offsetResult] = count > 0 ? accum / count : 0;
            offsetResult++;
            offsetBuffer = nextOffsetBuffer;
        }
        return result;
    }

    function floatTo16BitPCM(input) {
        const buffer = new ArrayBuffer(input.length * 2);
        const view = new DataView(buffer);
        for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        }
        return buffer;
    }

    async function startRecording() {
        try {
            // Request microphone access
            audioStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl:  true,
                }
            });

            // Set up Web Audio API for level metering and get the source node
            const source = setupAudioAnalyser(audioStream);

            // Create script processor (bufferSize 4096 is standard and stable)
            scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);
            source.connect(scriptProcessor);
            scriptProcessor.connect(audioContext.destination);

            scriptProcessor.onaudioprocess = (e) => {
                if (!isRecording) return;
                const inputData = e.inputBuffer.getChannelData(0);
                const resampled = resample(inputData, audioContext.sampleRate, 16000);
                const pcmBuffer = floatTo16BitPCM(resampled);

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(pcmBuffer);
                }
            };

            isRecording = true;

            // Update UI
            updateRecordingUI(true);
            console.log(`[Recorder] Started continuous PCM streaming (resampled from ${audioContext.sampleRate}Hz to 16000Hz)`);

        } catch (err) {
            console.error('[Recorder] Failed to start:', err);

            if (err.name === 'NotAllowedError') {
                addErrorCard(
                    'Microphone access denied. Please allow microphone access in your browser settings and try again.',
                );
            } else {
                addErrorCard(`Microphone error: ${err.message}`);
            }
        }
    }

    function stopRecording() {
        isRecording = false;

        // Notify server that recording has stopped (to flush remaining buffer)
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'stop' }));
        }

        if (scriptProcessor) {
            scriptProcessor.disconnect();
            scriptProcessor = null;
        }

        if (audioStream) {
            audioStream.getTracks().forEach(t => t.stop());
            audioStream = null;
        }

        stopAudioAnalyser();
        updateRecordingUI(false);
        console.log('[Recorder] Stopped');
    }

    function getSupportedMimeType() {
        return 'audio/wav'; // Deprecated
    }


    // ═══════════════════════════════════════════════════════════════════════════
    // Audio Level Visualisation
    // ═══════════════════════════════════════════════════════════════════════════

    function setupAudioAnalyser(stream) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const source = audioContext.createMediaStreamSource(stream);
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.7;
        source.connect(analyser);

        // Start level animation loop
        updateLevel();

        return source;
    }

    function updateLevel() {
        if (!analyser) return;

        const data = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(data);

        // Compute average level (0–255)
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
            sum += data[i];
        }
        const avg = sum / data.length;
        const pct = Math.min((avg / 128) * 100, 100);

        // Update meter
        dom.levelFill.style.width = pct + '%';
        dom.levelFill.className = 'level-fill' +
            (pct > 60 ? ' very-loud' : pct > 25 ? ' loud' : '');
        dom.levelLabel.textContent = Math.round(avg);

        levelAnimFrame = requestAnimationFrame(updateLevel);
    }

    function stopAudioAnalyser() {
        if (levelAnimFrame) {
            cancelAnimationFrame(levelAnimFrame);
            levelAnimFrame = null;
        }
        if (audioContext) {
            audioContext.close().catch(() => {});
            audioContext = null;
            analyser = null;
        }
        dom.levelFill.style.width = '0%';
        dom.levelLabel.textContent = '—';
    }


    // ═══════════════════════════════════════════════════════════════════════════
    // UI Updates
    // ═══════════════════════════════════════════════════════════════════════════

    function updateRecordingUI(recording) {
        const btn = dom.recordBtn;
        const micIcon = btn.querySelector('.mic-icon');
        const stopIcon = btn.querySelector('.stop-icon');

        if (recording) {
            btn.classList.add('recording');
            micIcon.classList.add('hidden');
            stopIcon.classList.remove('hidden');
            dom.recordBtnLabel.textContent = 'Stop Recording';
            dom.levelContainer.classList.add('visible');
        } else {
            btn.classList.remove('recording');
            micIcon.classList.remove('hidden');
            stopIcon.classList.add('hidden');
            dom.recordBtnLabel.textContent = 'Start Recording';
            dom.levelContainer.classList.remove('visible');
        }
    }

    function updateStats() {
        dom.statChunks.textContent = stats.chunks;
        dom.statSkipped.textContent = stats.skipped;
        dom.statLatency.textContent = stats.lastLatency !== null
            ? stats.lastLatency + 's'
            : '—';
        dom.statErrors.textContent = stats.errors;
    }

    function resetStats() {
        stats = { chunks: 0, skipped: 0, errors: 0, lastLatency: null };
        updateStats();
    }


    // ─── Result Cards ───────────────────────────────────────────────────────────

    function hideEmptyState() {
        if (dom.resultsEmpty) {
            dom.resultsEmpty.style.display = 'none';
        }
    }

    function showEmptyState() {
        if (dom.resultsEmpty) {
            dom.resultsEmpty.style.display = 'flex';
        }
    }

    function addTranscriptionCard(msg) {
        hideEmptyState();

        const card = document.createElement('div');
        card.className = 'result-card';

        const time = new Date().toLocaleTimeString('en-IN', {
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
        });

        card.innerHTML = `
            <div class="result-meta">
                <span class="chunk-badge">#${String(msg.chunk_num).padStart(3, '0')}</span>
                <span>${time}</span>
                <span class="latency-badge">${msg.latency}s</span>
            </div>
            <div class="result-source">
                <span class="lang-tag">${msg.lang}</span>
                <span class="source-text">${escapeHtml(msg.transcript)}</span>
            </div>
            <div class="result-translation">
                <span class="lang-tag">English</span>
                <span class="translation-text">${escapeHtml(msg.translation)}</span>
            </div>
        `;

        // Insert at top
        dom.resultsContainer.insertBefore(card, dom.resultsContainer.firstChild);

        // Auto-scroll to top
        dom.resultsContainer.scrollTop = 0;
    }

    function addProcessingCard(chunkNum) {
        hideEmptyState();

        const card = document.createElement('div');
        card.className = 'result-card processing';
        card.id = 'processingCard';

        card.innerHTML = `
            <div class="processing-indicator">
                <div class="processing-dots">
                    <span></span><span></span><span></span>
                </div>
                Processing chunk #${String(chunkNum).padStart(3, '0')}…
            </div>
        `;

        dom.resultsContainer.insertBefore(card, dom.resultsContainer.firstChild);
    }

    function removeProcessingCards() {
        const existing = document.querySelectorAll('.result-card.processing');
        existing.forEach(el => el.remove());
    }

    function addErrorCard(message, chunkNum) {
        hideEmptyState();

        const card = document.createElement('div');
        card.className = 'result-card error-card';

        const prefix = chunkNum ? `Chunk #${String(chunkNum).padStart(3, '0')}: ` : '';

        card.innerHTML = `
            <div class="error-message">
                <span>❌</span>
                <span>${prefix}${escapeHtml(message)}</span>
            </div>
        `;

        dom.resultsContainer.insertBefore(card, dom.resultsContainer.firstChild);
    }

    function clearResults() {
        // Remove all result cards but keep empty state
        const cards = dom.resultsContainer.querySelectorAll('.result-card');
        cards.forEach(card => card.remove());
        showEmptyState();
        resetStats();
    }


    // ─── Text Translation ───────────────────────────────────────────────────────

    function sendTextTranslation() {
        const text = dom.textInput.value.trim();
        if (!text) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            dom.textOutput.innerHTML = '<span class="text-output-placeholder" style="color:var(--accent-rose)">Not connected to server</span>';
            return;
        }

        dom.textOutput.innerHTML = '<span class="text-output-placeholder">Translating…</span>';
        ws.send(JSON.stringify({
            type: 'translate_text',
            text: text,
            lang: selectedLang,
        }));
    }

    function showTextTranslation(msg) {
        dom.textOutput.innerHTML = `
            <div>
                <div class="translated-result">${escapeHtml(msg.translation)}</div>
                <div class="translation-latency">${msg.lang} → English · ${msg.latency}s</div>
            </div>
        `;
    }


    // ═══════════════════════════════════════════════════════════════════════════
    // Event Listeners
    // ═══════════════════════════════════════════════════════════════════════════

    function init() {
        // Language selection
        document.querySelectorAll('.lang-card').forEach((card) => {
            card.addEventListener('click', () => {
                // Update active state
                document.querySelectorAll('.lang-card').forEach(c => c.classList.remove('active'));
                card.classList.add('active');

                selectedLang = card.dataset.lang;
                sendConfig(selectedLang);

                // If recording, stop and restart to apply new language
                if (isRecording) {
                    stopRecording();
                    // Small delay then restart
                    setTimeout(() => startRecording(), 500);
                }
            });
        });

        // Record button
        dom.recordBtn.addEventListener('click', () => {
            if (isRecording) {
                stopRecording();
            } else {
                startRecording();
            }
        });

        // Clear button
        dom.clearBtn.addEventListener('click', clearResults);

        // Text translate
        dom.translateBtn.addEventListener('click', sendTextTranslation);
        dom.textInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendTextTranslation();
            }
        });

        // Connect WebSocket
        connectWebSocket();
    }


    // ─── Utility ────────────────────────────────────────────────────────────────

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }


    // ─── Boot ───────────────────────────────────────────────────────────────────
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
