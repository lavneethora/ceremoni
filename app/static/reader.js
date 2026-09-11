let currentSessionId = null;
let queue = [];
let currentStudent = null;
let isPlaying = false;

const sessionSelect = document.getElementById('session-select');
const stage = document.getElementById('reader-stage');
const empty = document.getElementById('reader-empty');
const currentName = document.getElementById('reader-current-name');
const currentMeta = document.getElementById('reader-current-meta');
const playBtn = document.getElementById('reader-play-btn');
const upcomingList = document.getElementById('reader-upcoming-list');
const audio = document.getElementById('reader-audio');

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
    if (!currentSessionId) {
        showEmpty('Pick a session to begin.');
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
    const parts = [currentStudent.college, currentStudent.major].filter(Boolean);
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

    playBtn.disabled = !currentStudent || isPlaying;
}

function showEmpty(msg) {
    stage.hidden = true;
    empty.hidden = false;
    empty.textContent = msg;
}

async function playNext() {
    if (!currentStudent || isPlaying) return;
    isPlaying = true;
    playBtn.disabled = true;
    const studentId = currentStudent.id;
    try {
        const resp = await fetch('/admin/api/ceremony/play/' + studentId, {method: 'POST'});
        const data = await resp.json();
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
    }
}

playBtn.addEventListener('click', playNext);

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
