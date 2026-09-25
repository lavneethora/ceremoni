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
    if (!currentSessionId) {
        showEmpty('Pick a session to begin.');
        render();
        return;
    }
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
        e.preventDefault();
        playNext();
    }
});

init();
