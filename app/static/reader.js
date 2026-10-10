let currentSessionId = null;
let queue = [];
let currentStudent = null;
let isPlaying = false;
let history = []; // stack of student objects played this session (this browser tab)

const HONORS_LABELS = {honors: 'With honors', highest_honors: 'With highest honors'};

const sessionSelect = document.getElementById('session-select');
const stage = document.getElementById('reader-stage');
const empty = document.getElementById('reader-empty');
const currentName = document.getElementById('reader-current-name');
const currentMeta = document.getElementById('reader-current-meta');
const playBtn = document.getElementById('reader-play-btn');
const backBtn = document.getElementById('reader-back-btn');
const upcomingList = document.getElementById('reader-upcoming-list');
const audio = document.getElementById('reader-audio');
const notice = document.getElementById('reader-notice');

// Roster sessions only announce a current clip; other sessions report no announcement state
function isBlocked(student) {
    return !!student && !!student.announcement && student.announcement !== 'ready';
}

function showNotice(text) {
    notice.textContent = text;
    notice.hidden = false;
}

function hideNotice() {
    notice.hidden = true;
}

async function init() {
    const resp = await fetch('/admin/api/events');
    const events = await resp.json();
    while (sessionSelect.options.length > 1) sessionSelect.remove(1);
    for (const event of events) {
        for (const session of event.sessions) {
            const opt = document.createElement('option');
            opt.value = session.id;
            opt.textContent = event.name + ' — ' + session.label;
            sessionSelect.appendChild(opt);
        }
    }
}

sessionSelect.addEventListener('change', async () => {
    currentSessionId = sessionSelect.value;
    history = [];
    stopScanning();
    announced = [];
    scanCurrent.hidden = true;
    scanNotice.hidden = true;
    readerModes.hidden = !currentSessionId;
    if (!currentSessionId) {
        scanSection.hidden = true;
        showEmpty('Pick a session to begin.');
        render();
        return;
    }
    scanSection.hidden = !scanMode;
    await loadQueue();
});

async function loadQueue() {
    const resp = await fetch('/admin/api/ceremony/upcoming?session_id=' + currentSessionId + '&limit=4');
    const data = await resp.json();
    queue = data.queue || [];
    render();
}

function render() {
    backBtn.disabled = history.length === 0 || isPlaying;

    // Scan mode owns the screen; the queue view would otherwise reappear
    // every time the queue reloads behind it.
    if (scanMode) {
        stage.hidden = true;
        empty.hidden = true;
        return;
    }

    if (queue.length === 0) {
        currentStudent = null;
        stage.hidden = true;
        empty.hidden = false;
        empty.textContent = 'Session complete. No more students in the queue.';
        return;
    }

    empty.hidden = true;
    stage.hidden = false;

    currentStudent = queue[0];
    currentName.textContent = currentStudent.typed_name;
    const parts = [currentStudent.college, currentStudent.major, HONORS_LABELS[currentStudent.honors_level]].filter(Boolean);
    currentMeta.textContent = parts.join(' — ');

    upcomingList.replaceChildren();
    for (const s of queue.slice(1, 4)) {
        const li = document.createElement('li');
        const nameSpan = document.createElement('span');
        nameSpan.className = 'reader-upcoming-name';
        nameSpan.textContent = s.typed_name;
        const metaSpan = document.createElement('span');
        metaSpan.className = 'reader-upcoming-meta';
        metaSpan.textContent = s.major || s.college || '';
        li.appendChild(nameSpan);
        li.appendChild(metaSpan);
        upcomingList.appendChild(li);
    }

    const blocked = isBlocked(currentStudent);
    playBtn.disabled = !currentStudent || isPlaying || blocked;
    if (blocked) {
        showNotice(`The announcement for ${currentStudent.typed_name} is not ready. Ask an admin to generate announcements.`);
    } else {
        hideNotice();
    }
}

function showEmpty(msg) {
    stage.hidden = true;
    empty.hidden = false;
    empty.textContent = msg;
}

async function playNext() {
    if (!currentStudent || isPlaying || isBlocked(currentStudent)) return;
    isPlaying = true;
    playBtn.disabled = true;
    backBtn.disabled = true;
    const student = currentStudent;
    try {
        const resp = await fetch('/admin/api/ceremony/play/' + student.id + '?session_id=' + currentSessionId, {method: 'POST'});
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            isPlaying = false;
            await loadQueue();
            showNotice(err.detail || 'Could not play this student.');
            return;
        }
        const data = await resp.json();
        history.push(student);
        if (data.audio_url) {
            audio.src = data.audio_url;
            try {
                await audio.play();
            } catch (e) {
                // autoplay policy may reject, user will see the audio element ready
                console.warn('audio.play() rejected', e);
            }
            audio.onended = async () => {
                isPlaying = false;
                await loadQueue();
            };
            audio.onerror = async () => {
                isPlaying = false;
                await loadQueue();
            };
        } else {
            // No audio available, still advance
            isPlaying = false;
            await loadQueue();
        }
    } catch (e) {
        console.error('play failed', e);
        isPlaying = false;
        playBtn.disabled = false;
        backBtn.disabled = history.length === 0;
    }
}

async function goBack() {
    if (history.length === 0 || isPlaying) return;
    isPlaying = true;
    playBtn.disabled = true;
    backBtn.disabled = true;
    const student = history.pop();
    try {
        await fetch('/admin/api/ceremony/unplay/' + student.id + '?session_id=' + currentSessionId, {method: 'POST'});
    } catch (e) {
        console.error('unplay failed', e);
        history.push(student); // put it back so the user can retry
    } finally {
        isPlaying = false;
        await loadQueue();
    }
}

playBtn.addEventListener('click', playNext);
backBtn.addEventListener('click', goBack);

document.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !e.repeat) {
        const t = e.target;
        // Don't hijack space when a form control is focused
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
        if (scanMode) return;
        e.preventDefault();
        playNext();
    }
});

init();

// --- Scan mode -------------------------------------------------------------
// The announcer scans each student's card as they reach the stage, so the
// order announced is simply whoever is standing there. Nothing is captured in
// advance and a late arrival is not a special case.

const readerModes = document.getElementById('reader-modes');
const scanSection = document.getElementById('reader-scan');
const scanStart = document.getElementById('scan-start');
const scanStartBtn = document.getElementById('scan-start-btn');
const scanLive = document.getElementById('scan-live');
const scanVideo = document.getElementById('scan-video');
const scanCurrent = document.getElementById('scan-current');
const scanPosition = document.getElementById('scan-position');
const scanName = document.getElementById('scan-name');
const scanMeta = document.getElementById('scan-meta');
const scanNotice = document.getElementById('scan-notice');
const scanCount = document.getElementById('scan-count');
const scanStopBtn = document.getElementById('scan-stop-btn');
const scanRecent = document.getElementById('scan-recent');
const queueStage = document.getElementById('reader-stage');

// A camera reports the same code on every frame it stays in view, so the same
// card is ignored for a few seconds rather than announced over and over.
const SAME_CARD_IGNORE_MS = 5000;

let scanMode = true;
let stream = null;
let detector = null;
let scanTimer = null;
let decoding = false;
let lastCode = null;
let lastCodeAt = 0;
let announced = [];

function setMode(mode) {
    scanMode = mode === 'scan';
    for (const btn of document.querySelectorAll('.reader-mode')) {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    }
    scanSection.hidden = !scanMode;
    if (scanMode) {
        queueStage.hidden = true;
        empty.hidden = true;
    } else {
        stopScanning();
        render();
    }
}

function showScanNotice(text) {
    scanNotice.textContent = text;
    scanNotice.hidden = false;
}

function renderRecent() {
    scanRecent.replaceChildren();
    for (const item of announced.slice(0, 5)) {
        const li = document.createElement('li');
        const name = document.createElement('span');
        name.textContent = item.typed_name;
        const pos = document.createElement('span');
        pos.textContent = '#' + item.position;
        li.append(name, pos);
        scanRecent.appendChild(li);
    }
    scanCount.textContent = announced.length
        ? `${announced.length} announced` : 'Nobody announced yet';
}

async function startScanning() {
    if (!currentSessionId) {
        showScanNotice('Pick a session first.');
        return;
    }
    // This runs inside a click, so playing the (still empty) audio element here
    // buys a user gesture for every later decode-triggered play. Without it the
    // browser blocks autoplay and the failure is silent.
    try {
        audio.muted = true;
        await audio.play().catch(() => {});
        audio.pause();
        audio.currentTime = 0;
        audio.muted = false;
    } catch (e) {
        // not fatal, the first scan may just need a tap
    }

    try {
        stream = await navigator.mediaDevices.getUserMedia({
            video: {facingMode: 'environment', width: {ideal: 1280}},
            audio: false,
        });
    } catch (e) {
        showScanNotice('Could not open the camera: ' + e.message);
        return;
    }

    scanVideo.srcObject = stream;
    await scanVideo.play().catch(() => {});

    if ('BarcodeDetector' in window) {
        try {
            detector = new window.BarcodeDetector({formats: ['qr_code']});
        } catch (e) {
            detector = null;
        }
    }

    scanStart.hidden = true;
    scanLive.hidden = false;
    scanNotice.hidden = true;
    renderRecent();
    scanTimer = setInterval(tick, 250);
}

function stopScanning() {
    clearInterval(scanTimer);
    scanTimer = null;
    if (stream) {
        for (const track of stream.getTracks()) track.stop();
        stream = null;
    }
    scanVideo.srcObject = null;
    scanLive.hidden = true;
    scanStart.hidden = false;
}

const frame = document.createElement('canvas');

async function readFrame() {
    if (!scanVideo.videoWidth) return null;
    if (detector) {
        try {
            const found = await detector.detect(scanVideo);
            return found.length ? found[0].rawValue : null;
        } catch (e) {
            detector = null;  // fall through to jsQR from here on
        }
    }
    frame.width = scanVideo.videoWidth;
    frame.height = scanVideo.videoHeight;
    const ctx = frame.getContext('2d', {willReadFrequently: true});
    ctx.drawImage(scanVideo, 0, 0, frame.width, frame.height);
    const pixels = ctx.getImageData(0, 0, frame.width, frame.height);
    const found = window.jsQR ? window.jsQR(pixels.data, frame.width, frame.height) : null;
    return found ? found.data : null;
}

async function tick() {
    if (decoding || isPlaying) return;
    decoding = true;
    try {
        const code = await readFrame();
        if (!code) return;
        const now = Date.now();
        if (code === lastCode && now - lastCodeAt < SAME_CARD_IGNORE_MS) return;
        lastCode = code;
        lastCodeAt = now;
        await announceCard(code);
    } catch (e) {
        console.warn('scan failed', e);
    } finally {
        decoding = false;
    }
}

async function announceCard(code) {
    isPlaying = true;
    try {
        const resp = await fetch('/admin/api/ceremony/scan', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code}),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showScanNotice(data.detail || 'That card could not be used.');
            isPlaying = false;
            return;
        }

        scanNotice.hidden = true;
        scanCurrent.hidden = false;
        scanPosition.textContent = data.already_played
            ? `Already announced, position ${data.position}` : `Now announcing, ${data.position}`;
        scanName.textContent = data.typed_name;
        scanMeta.textContent = [data.major, HONORS_LABELS[data.honors_level]].filter(Boolean).join(' — ');

        if (!data.already_played) {
            announced.unshift(data);
            renderRecent();
        }

        audio.src = data.audio_url;
        try {
            await audio.play();
        } catch (e) {
            showScanNotice('Tap the page once to allow audio, then scan again.');
            isPlaying = false;
            return;
        }
        audio.onended = () => { isPlaying = false; };
        audio.onerror = () => {
            showScanNotice('The announcement audio could not be played.');
            isPlaying = false;
        };
    } catch (e) {
        showScanNotice('Connection problem, try that card again.');
        isPlaying = false;
    }
}

for (const btn of document.querySelectorAll('.reader-mode')) {
    btn.addEventListener('click', () => {
        const mode = btn.dataset.mode;
        setMode(mode);
        // Scan mode is already selected by default, so clicking its tab used to
        // do nothing at all and looked broken. Clicking it is also a gesture,
        // which is what the audio element needs unlocking with, so start the
        // camera here rather than making the announcer find a second button.
        if (mode === 'scan' && currentSessionId && !stream) startScanning();
    });
}
scanStartBtn.addEventListener('click', startScanning);
scanStopBtn.addEventListener('click', stopScanning);
window.addEventListener('pagehide', stopScanning);
