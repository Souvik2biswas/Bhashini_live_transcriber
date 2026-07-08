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
    let activeTreeTab = 'indo-aryan';
    let currentViewMode = 'grid';

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
        languageTreeContainer: $('languageTreeContainer'),
        treeViewport:    $('treeViewport'),
        treeTabs:        $('treeTabs'),
        viewToggle:      $('viewToggle'),
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
            updateMicrophoneState();

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
            updateMicrophoneState();

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
                renderLanguageGrid(msg.languages);
                renderLanguageTree(activeTreeTab);
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

    let supportedLanguages = {};

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
            dom.levelContainer.classList.remove('visible');
            updateMicrophoneState();
        }
    }

    const FAMILY_ORDER = [
        "Indo-Aryan",
        "Dravidian",
        "Sino-Tibetan",
        "Austroasiatic",
        "Other"
    ];

    const FAMILY_NAMES = {
        "Indo-Aryan": "Indo-Aryan Branch",
        "Dravidian": "Dravidian Family",
        "Sino-Tibetan": "Sino-Tibetan Family",
        "Austroasiatic": "Austroasiatic Family",
        "Other": "Other / Global"
    };

    function applyActiveFilter() {
        const activePill = document.querySelector('.filter-pill.active');
        if (!activePill) return;

        const activeFamily = activePill.dataset.family;
        document.querySelectorAll('.family-section-group').forEach(group => {
            if (activeFamily === 'all' || group.dataset.family === activeFamily) {
                group.classList.remove('hidden');
            } else {
                group.classList.add('hidden');
            }
        });
    }

    function renderLanguageGrid(languages) {
        supportedLanguages = languages;
        if (!dom.languageGrid) return;

        dom.languageGrid.innerHTML = '';
        dom.languageGrid.style.display = 'block';

        FAMILY_ORDER.forEach(family => {
            const familyLangs = Object.entries(languages).filter(([code, info]) => {
                const f = info.family || "Other";
                return f === family;
            });

            if (familyLangs.length === 0) return;

            const sectionGroup = document.createElement('div');
            sectionGroup.className = 'family-section-group';
            sectionGroup.dataset.family = family;

            const header = document.createElement('h3');
            header.className = 'family-header';
            header.textContent = FAMILY_NAMES[family] || family;
            sectionGroup.appendChild(header);

            const grid = document.createElement('div');
            grid.className = 'language-grid';

            familyLangs.forEach(([code, info]) => {
                const card = document.createElement('button');
                card.className = 'lang-card';
                if (code === selectedLang) {
                    card.classList.add('active');
                }
                card.dataset.lang = code;
                card.id = `lang-${code}`;

                const tagHtml = !info.asr_supported ? '\n                <span class="lang-tag">Text Only</span>' : '';
                card.innerHTML = `
                    <span class="lang-native">${escapeHtml(info.native)}</span>
                    <span class="lang-name">${escapeHtml(info.name)}</span>${tagHtml}
                    <span class="lang-check">✓</span>
                `;

                card.addEventListener('click', () => {
                    if (selectedLang === code) return;

                    document.querySelectorAll('.lang-card').forEach(c => c.classList.remove('active'));
                    card.classList.add('active');

                    selectedLang = code;
                    sendConfig(selectedLang);
                    updateMicrophoneState();

                    // If recording, stop and restart to apply new language
                    if (isRecording) {
                        stopRecording();
                        setTimeout(() => {
                            if (supportedLanguages[selectedLang]?.asr_supported) {
                                startRecording();
                            }
                        }, 500);
                    }
                });

                grid.appendChild(card);
            });

            sectionGroup.appendChild(grid);
            dom.languageGrid.appendChild(sectionGroup);
        });

        applyActiveFilter();
        updateMicrophoneState();
    }

    // ─── SVG Language Tree Data ──────────────────────────────────────────────────
    const INDO_ARYAN_TREE = {
        nodes: {
            vedic_sanskrit: { x: 450, y: 50, label: "Vedic Sanskrit", type: "ancient", color: "#EF4444" },
            sanskrit: { x: 580, y: 50, label: "Sanskrit", code: "sa", type: "supported", color: "#F59E0B" },
            prakrit: { x: 500, y: 150, label: "Prakrit", type: "ancient", color: "#EC4899" },
            elu: { x: 250, y: 100, label: "Elu", type: "intermediate", color: "#4B5563" },
            dhivehi: { x: 150, y: 100, label: "Dhivehi", type: "unsupported", color: "#4B5563" },
            sinhala: { x: 300, y: 50, label: "Sinhala", type: "unsupported", color: "#4B5563" },
            vedda: { x: 220, y: 40, label: "Vedda", type: "unsupported", color: "#4B5563" },
            maharashtri: { x: 220, y: 200, label: "Maharashtri", type: "intermediate", color: "#0EA5E9" },
            marathi: { x: 120, y: 170, label: "Marathi", code: "mr", type: "supported", color: "#0EA5E9" },
            konkani: { x: 120, y: 230, label: "Konkani", code: "gom", type: "supported", color: "#0EA5E9" },
            pali: { x: 350, y: 240, label: "Pali", type: "ancient", color: "#10B981" },
            gandhari: { x: 620, y: 240, label: "Gandhari", type: "ancient", color: "#9CA3AF" },
            shauraseni: { x: 500, y: 260, label: "Shauraseni", type: "ancient", color: "#F97316" },
            north_indic: { x: 180, y: 320, label: "North Indic", type: "intermediate", color: "#EF4444" },
            dogri: { x: 200, y: 260, label: "Dogri", code: "doi", type: "supported", color: "#EF4444" },
            punjabi: { x: 100, y: 320, label: "Punjabi", code: "pa", type: "supported", color: "#EF4444" },
            sindhi: { x: 160, y: 380, label: "Sindhi", code: "sd", type: "supported", color: "#EF4444" },
            west_indic: { x: 250, y: 450, label: "West Indic", type: "intermediate", color: "#0EA5E9" },
            gujarati: { x: 250, y: 530, label: "Gujarati", code: "gu", type: "supported", color: "#0EA5E9" },
            marwari: { x: 170, y: 500, label: "Marwari", type: "unsupported", color: "#0EA5E9" },
            romani: { x: 320, y: 500, label: "Romani", type: "unsupported", color: "#0EA5E9" },
            dardic: { x: 420, y: 350, label: "Dardic", type: "intermediate", color: "#4B5563" },
            kashmiri: { x: 350, y: 380, label: "Kashmiri", code: "ks", type: "supported", color: "#4B5563" },
            shina: { x: 420, y: 420, label: "Shina", type: "unsupported", color: "#4B5563" },
            pahari: { x: 580, y: 360, label: "Pahari", type: "intermediate", color: "#10B981" },
            nepali: { x: 540, y: 420, label: "Nepali", code: "ne", type: "supported", color: "#10B981" },
            kumaoni: { x: 650, y: 340, label: "Kumaoni", type: "unsupported", color: "#10B981" },
            garhwali: { x: 620, y: 420, label: "Garhwali", type: "unsupported", color: "#10B981" },
            hindustani: { x: 800, y: 320, label: "Hindustani", type: "intermediate", color: "#F59E0B" },
            hindi: { x: 740, y: 380, label: "Hindi", code: "hi", type: "supported", color: "#F59E0B" },
            urdu: { x: 860, y: 380, label: "Urdu", code: "ur", type: "supported", color: "#F59E0B" },
            haryanvi: { x: 690, y: 440, label: "Haryanvi", type: "unsupported", color: "#F59E0B" },
            rekhta: { x: 800, y: 440, label: "Rekhta", type: "unsupported", color: "#F59E0B" },
            dakhini: { x: 910, y: 440, label: "Dakhini", type: "unsupported", color: "#F59E0B" },
            magadhi: { x: 800, y: 130, label: "Magadhi", type: "intermediate", color: "#10B981" },
            odia: { x: 880, y: 70, label: "Odia", code: "or", type: "supported", color: "#10B981" },
            bangla: { x: 910, y: 120, label: "Bangla", code: "bn", type: "supported", color: "#10B981" },
            assamese: { x: 880, y: 170, label: "Assamese", code: "as", type: "supported", color: "#10B981" },
            bihari: { x: 780, y: 190, label: "Bihari", type: "intermediate", color: "#065F46" },
            bhojpuri: { x: 700, y: 190, label: "Bhojpuri", type: "unsupported", color: "#065F46" },
            maithili: { x: 760, y: 250, label: "Maithili", code: "mai", type: "supported", color: "#065F46" },
            magahi: { x: 830, y: 220, label: "Magahi", type: "unsupported", color: "#065F46" }
        },
        links: [
            { from: "vedic_sanskrit", to: "prakrit" },
            { from: "vedic_sanskrit", to: "sanskrit" },
            { from: "prakrit", to: "elu" },
            { from: "prakrit", to: "maharashtri" },
            { from: "prakrit", to: "pali" },
            { from: "prakrit", to: "shauraseni" },
            { from: "prakrit", to: "gandhari" },
            { from: "prakrit", to: "magadhi" },
            { from: "elu", to: "dhivehi" },
            { from: "elu", to: "sinhala" },
            { from: "elu", to: "vedda" },
            { from: "maharashtri", to: "marathi" },
            { from: "maharashtri", to: "konkani" },
            { from: "shauraseni", to: "north_indic" },
            { from: "shauraseni", to: "west_indic" },
            { from: "shauraseni", to: "dardic" },
            { from: "shauraseni", to: "pahari" },
            { from: "shauraseni", to: "hindustani" },
            { from: "north_indic", to: "dogri" },
            { from: "north_indic", to: "punjabi" },
            { from: "north_indic", to: "sindhi" },
            { from: "west_indic", to: "marwari" },
            { from: "west_indic", to: "gujarati" },
            { from: "west_indic", to: "romani" },
            { from: "dardic", to: "kashmiri" },
            { from: "dardic", to: "shina" },
            { from: "pahari", to: "nepali" },
            { from: "pahari", to: "kumaoni" },
            { from: "pahari", to: "garhwali" },
            { from: "hindustani", to: "hindi" },
            { from: "hindustani", to: "urdu" },
            { from: "hindi", to: "haryanvi" },
            { from: "urdu", to: "rekhta" },
            { from: "urdu", to: "dakhini" },
            { from: "magadhi", to: "odia" },
            { from: "magadhi", to: "bangla" },
            { from: "magadhi", to: "assamese" },
            { from: "magadhi", to: "bihari" },
            { from: "bihari", to: "bhojpuri" },
            { from: "bihari", to: "maithili" },
            { from: "bihari", to: "magahi" }
        ]
    };

    const DRAVIDIAN_TREE = {
        nodes: {
            proto_dravidian: { x: 500, y: 70, label: "Proto Dravidian", type: "ancient", color: "#1F2937" },
            central_dravidian: { x: 300, y: 150, label: "Central Dravidian", type: "intermediate", color: "#065F46" },
            parji: { x: 180, y: 100, label: "Parji", type: "unsupported", color: "#065F46" },
            kolami: { x: 130, y: 150, label: "Kolami", type: "unsupported", color: "#065F46" },
            naiki: { x: 180, y: 200, label: "Naiki", type: "unsupported", color: "#065F46" },
            manda: { x: 360, y: 200, label: "Manda", type: "unsupported", color: "#065F46" },
            kui: { x: 340, y: 260, label: "Kui", type: "unsupported", color: "#065F46" },
            gondi: { x: 280, y: 290, label: "Gondi", type: "unsupported", color: "#065F46" },
            koraga: { x: 200, y: 280, label: "Koraga", type: "unsupported", color: "#065F46" },
            telugu: { x: 120, y: 240, label: "Telugu", code: "te", type: "supported", color: "#16A34A" },
            northern_dravidian: { x: 700, y: 150, label: "Northern Dravidian", type: "intermediate", color: "#9D174D" },
            kurukh: { x: 800, y: 110, label: "Kurukh", type: "unsupported", color: "#9D174D" },
            malto: { x: 830, y: 170, label: "Malto", type: "unsupported", color: "#9D174D" },
            brahui: { x: 740, y: 230, label: "Brahui", type: "unsupported", color: "#9D174D" },
            southern_dravidian: { x: 500, y: 230, label: "Southern Dravidian", type: "intermediate", color: "#1E3A8A" },
            tulu: { x: 650, y: 230, label: "Tulu", type: "unsupported", color: "#1D4ED8" },
            tamil_kannada: { x: 500, y: 320, label: "Tamil-Kannada", type: "intermediate", color: "#1D4ED8" },
            toda: { x: 200, y: 370, label: "Toda", type: "unsupported", color: "#1D4ED8" },
            kota: { x: 200, y: 440, label: "Kota", type: "unsupported", color: "#1D4ED8" },
            kannada: { x: 800, y: 370, label: "Kannada", code: "kn", type: "supported", color: "#1D4ED8" },
            kodagu: { x: 800, y: 440, label: "Kodagu", type: "unsupported", color: "#1D4ED8" },
            irula: { x: 620, y: 480, label: "Irula", type: "unsupported", color: "#1D4ED8" },
            malayalam: { x: 380, y: 480, label: "Malayalam", code: "ml", type: "supported", color: "#1D4ED8" },
            tamil: { x: 500, y: 480, label: "Tamil", code: "ta", type: "supported", color: "#1D4ED8" }
        },
        links: [
            { from: "proto_dravidian", to: "central_dravidian" },
            { from: "proto_dravidian", to: "northern_dravidian" },
            { from: "proto_dravidian", to: "southern_dravidian" },
            { from: "central_dravidian", to: "parji" },
            { from: "central_dravidian", to: "kolami" },
            { from: "central_dravidian", to: "naiki" },
            { from: "central_dravidian", to: "manda" },
            { from: "central_dravidian", to: "kui" },
            { from: "central_dravidian", to: "gondi" },
            { from: "central_dravidian", to: "koraga" },
            { from: "central_dravidian", to: "telugu" },
            { from: "northern_dravidian", to: "kurukh" },
            { from: "northern_dravidian", to: "malto" },
            { from: "northern_dravidian", to: "brahui" },
            { from: "southern_dravidian", to: "tamil_kannada" },
            { from: "southern_dravidian", to: "tulu" },
            { from: "tamil_kannada", to: "toda" },
            { from: "tamil_kannada", to: "kota" },
            { from: "tamil_kannada", to: "kannada" },
            { from: "tamil_kannada", to: "kodagu" },
            { from: "tamil_kannada", to: "irula" },
            { from: "tamil_kannada", to: "malayalam" },
            { from: "tamil_kannada", to: "tamil" }
        ]
    };

    const SINO_TIBETAN_TREE = {
        nodes: {
            proto_sino_tibetan: { x: 500, y: 100, label: "Proto-Sino-Tibetan", type: "ancient", color: "#1F2937" },
            bodo: { x: 400, y: 250, label: "Bodo", code: "brx", type: "supported", color: "#8B5CF6" },
            manipuri: { x: 600, y: 250, label: "Manipuri", code: "mni", type: "supported", color: "#8B5CF6" }
        },
        links: [
            { from: "proto_sino_tibetan", to: "bodo" },
            { from: "proto_sino_tibetan", to: "manipuri" }
        ]
    };

    const AUSTROASIATIC_TREE = {
        nodes: {
            proto_austroasiatic: { x: 500, y: 100, label: "Proto-Austroasiatic", type: "ancient", color: "#1F2937" },
            santali: { x: 500, y: 250, label: "Santali", code: "sat", type: "supported", color: "#EC4899" }
        },
        links: [
            { from: "proto_austroasiatic", to: "santali" }
        ]
    };

    const TREE_DATA = {
        "indo-aryan": INDO_ARYAN_TREE,
        "dravidian": DRAVIDIAN_TREE,
        "sino-tibetan": SINO_TIBETAN_TREE,
        "austroasiatic": AUSTROASIATIC_TREE
    };

    function renderLanguageTree(familyId) {
        if (!dom.treeViewport) return;
        dom.treeViewport.innerHTML = '';

        const tree = TREE_DATA[familyId];
        if (!tree) return;

        const isSmall = familyId === 'sino-tibetan' || familyId === 'austroasiatic';
        const height = isSmall ? 380 : 580;

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', `0 0 1000 ${height}`);
        svg.className.baseVal = isSmall ? 'tree-svg small-tree' : 'tree-svg';

        // Draw links
        tree.links.forEach(link => {
            const fromNode = tree.nodes[link.from];
            const toNode = tree.nodes[link.to];
            if (!fromNode || !toNode) return;

            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            const midY = (fromNode.y + toNode.y) / 2;
            const d = `M ${fromNode.x} ${fromNode.y} C ${fromNode.x} ${midY}, ${toNode.x} ${midY}, ${toNode.x} ${toNode.y}`;
            path.setAttribute('d', d);
            path.className.baseVal = 'tree-link';

            if (fromNode.type === 'supported' && toNode.type === 'supported') {
                path.classList.add('highlighted');
            }

            svg.appendChild(path);
        });

        // Draw nodes
        Object.entries(tree.nodes).forEach(([id, node]) => {
            const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            g.className.baseVal = `tree-node ${node.type}`;
            if (node.code) {
                g.setAttribute('data-lang', node.code);
                if (node.code === selectedLang) {
                    g.classList.add('active');
                }
            }

            const isSupported = node.type === 'supported' && node.code;
            const radius = isSupported ? 22 : (node.type === 'unsupported' ? 16 : 18);

            // Node circle
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', node.x);
            circle.setAttribute('cy', node.y);
            circle.setAttribute('r', radius);
            circle.className.baseVal = 'node-circle';
            circle.style.fill = node.color;
            circle.style.stroke = node.color;
            circle.style.setProperty('--hover-shadow', `${node.color}99`);
            circle.style.setProperty('--active-shadow', node.color);

            g.appendChild(circle);

            // Readability halo
            const textBg = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            textBg.setAttribute('x', node.x);
            textBg.setAttribute('y', node.y + radius + 15);
            textBg.className.baseVal = 'node-label-bg';
            textBg.textContent = node.label;
            g.appendChild(textBg);

            // Foreground label
            const textFg = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            textFg.setAttribute('x', node.x);
            textFg.setAttribute('y', node.y + radius + 15);
            textFg.className.baseVal = 'node-label-text';
            textFg.textContent = node.label;
            g.appendChild(textFg);

            // Text only tag
            if (isSupported && supportedLanguages[node.code] && !supportedLanguages[node.code].asr_supported) {
                const badgeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                const badgeW = 60;
                const badgeH = 13;
                const badgeX = node.x - badgeW / 2;
                const badgeY = node.y + radius + 22;

                const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                rect.setAttribute('x', badgeX);
                rect.setAttribute('y', badgeY);
                rect.setAttribute('width', badgeW);
                rect.setAttribute('height', badgeH);
                rect.className.baseVal = 'node-tag-rect';

                const badgeTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                badgeTxt.setAttribute('x', node.x);
                badgeTxt.setAttribute('y', badgeY + 9);
                badgeTxt.className.baseVal = 'node-tag-text';
                badgeTxt.textContent = 'TEXT ONLY';

                badgeGroup.appendChild(rect);
                badgeGroup.appendChild(badgeTxt);
                g.appendChild(badgeGroup);
            }

            if (isSupported) {
                g.addEventListener('click', () => {
                    if (selectedLang === node.code) return;

                    selectedLang = node.code;

                    // Sync SVGs
                    document.querySelectorAll('.tree-node').forEach(nodeEl => {
                        if (nodeEl.getAttribute('data-lang') === selectedLang) {
                            nodeEl.classList.add('active');
                        } else {
                            nodeEl.classList.remove('active');
                        }
                    });

                    // Sync Grid cards
                    document.querySelectorAll('.lang-card').forEach(c => {
                        if (c.dataset.lang === selectedLang) {
                            c.classList.add('active');
                        } else {
                            c.classList.remove('active');
                        }
                    });

                    sendConfig(selectedLang);
                    updateMicrophoneState();

                    if (isRecording) {
                        stopRecording();
                        setTimeout(() => {
                            if (supportedLanguages[selectedLang]?.asr_supported) {
                                startRecording();
                            }
                        }, 500);
                    }
                });
            }

            svg.appendChild(g);
        });

        dom.treeViewport.appendChild(svg);
    }

    function updateMicrophoneState() {
        const langConfig = supportedLanguages[selectedLang];
        const btn = dom.recordBtn;
        if (!btn) return;

        if (langConfig && !langConfig.asr_supported) {
            btn.disabled = true;
            dom.recordBtnLabel.textContent = 'Speech Input Unsupported';
            btn.title = `Speech recognition is not supported for ${langConfig.name}`;
        } else if (ws && ws.readyState === WebSocket.OPEN) {
            btn.disabled = false;
            dom.recordBtnLabel.textContent = isRecording ? 'Stop Recording' : 'Start Recording';
            btn.title = '';
        } else {
            btn.disabled = true;
            dom.recordBtnLabel.textContent = 'Start Recording';
            btn.title = 'Connecting to server…';
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

        // Family filters
        document.querySelectorAll('.filter-pill').forEach(pill => {
            pill.addEventListener('click', () => {
                document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                applyActiveFilter();
            });
        });

        // View Toggle (Grid vs. Tree)
        if (dom.viewToggle) {
            dom.viewToggle.querySelectorAll('.toggle-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    dom.viewToggle.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');

                    currentViewMode = btn.dataset.view;
                    const familyFiltersEl = document.getElementById('familyFilters');

                    if (currentViewMode === 'tree') {
                        dom.languageGrid.classList.add('hidden');
                        if (familyFiltersEl) familyFiltersEl.classList.add('hidden');
                        dom.languageTreeContainer.classList.remove('hidden');
                        renderLanguageTree(activeTreeTab);
                    } else {
                        dom.languageTreeContainer.classList.add('hidden');
                        dom.languageGrid.classList.remove('hidden');
                        if (familyFiltersEl) familyFiltersEl.classList.remove('hidden');
                    }
                });
            });
        }

        // Tree Tab Switcher
        if (dom.treeTabs) {
            dom.treeTabs.querySelectorAll('.tree-tab').forEach(tab => {
                tab.addEventListener('click', () => {
                    dom.treeTabs.querySelectorAll('.tree-tab').forEach(t => t.classList.remove('active'));
                    tab.classList.add('active');

                    activeTreeTab = tab.dataset.tab;
                    renderLanguageTree(activeTreeTab);
                });
            });
        }

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
