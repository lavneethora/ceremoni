let currentSessionId = null;
let students = [];
let sortableInstance = null;

const sessionSelect = document.getElementById('session-select');
const playNextBtn = document.getElementById('play-next-btn');
const syncBtn = document.getElementById('sync-btn');
const resetBtn = document.getElementById('reset-btn');
const studentList = document.getElementById('student-list');
const nowPlaying = document.getElementById('now-playing');
const nowPlayingName = document.getElementById('now-playing-name');
const nowPlayingMeta = document.getElementById('now-playing-meta');
const audioPlayer = document.getElementById('audio-player');

// Load events and sessions on page load
async function init() {
    const resp = await fetch('/admin/api/events');
    const events = await resp.json();

    // Clear options
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
    if (currentSessionId) {
        await loadStudents();
        playNextBtn.disabled = false;
    } else {
        studentList.replaceChildren(createEmptyState('Select a session to view students'));
        playNextBtn.disabled = true;
    }
});

async function loadStudents() {
    const resp = await fetch('/admin/api/students?session_id=' + currentSessionId);
    students = await resp.json();
    renderStudents();
}

function createEmptyState(text) {
    const div = document.createElement('div');
    div.className = 'empty-state';
    div.textContent = text;
    return div;
}

function renderStudents() {
    studentList.replaceChildren();

    if (students.length === 0) {
        studentList.appendChild(createEmptyState('No students in this session'));
        return;
    }

    let currentCollege = '';

    for (const s of students) {
        if (s.college !== currentCollege) {
            currentCollege = s.college;
            const header = document.createElement('div');
            header.className = 'student-group-header';
            header.textContent = currentCollege;
            studentList.appendChild(header);
        }

        const row = document.createElement('div');
        row.className = 'student-row' + (s.played ? ' played' : '');
        row.dataset.id = s.id;

        const handle = document.createElement('span');
        handle.className = 'drag-handle';
        handle.textContent = '::';

        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = s.typed_name;

        const major = document.createElement('span');
        major.className = 'major';
        major.textContent = s.major || '';

        const status = document.createElement('span');
        const statusClass = s.has_audio ? 'ready' : (s.status === 'processing' ? 'processing' : 'pending');
        const statusText = s.has_audio ? 'Ready' : (s.status === 'processing' ? 'Processing' : 'Pending');
        status.className = 'status ' + statusClass;
        status.textContent = statusText;

        row.appendChild(handle);
        row.appendChild(name);
        row.appendChild(major);
        row.appendChild(status);
        studentList.appendChild(row);
    }

    // Initialize sortable
    if (sortableInstance) sortableInstance.destroy();
    sortableInstance = new Sortable(studentList, {
        handle: '.drag-handle',
        ghostClass: 'sortable-ghost',
        filter: '.student-group-header',
        onEnd: saveOrder,
    });
}

async function saveOrder() {
    const rows = studentList.querySelectorAll('.student-row');
    const order = Array.from(rows).map((row, i) => ({
        id: row.dataset.id,
        sort_order: i + 1,
    }));

    await fetch('/admin/api/students/reorder', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order}),
    });
}

playNextBtn.addEventListener('click', async () => {
    const resp = await fetch('/admin/api/ceremony/next?session_id=' + currentSessionId);
    const data = await resp.json();

    if (data.done) {
        nowPlaying.style.display = 'block';
        nowPlayingName.textContent = 'Ceremony complete';
        nowPlayingMeta.textContent = '';
        playNextBtn.disabled = true;
        return;
    }

    // Mark as played and get audio
    const playResp = await fetch('/admin/api/ceremony/play/' + data.id, {method: 'POST'});
    const playData = await playResp.json();

    // Show now playing
    nowPlaying.style.display = 'block';
    nowPlayingName.textContent = data.typed_name;
    nowPlayingMeta.textContent = (data.college || '') + ' — ' + (data.major || '');

    // Highlight active row
    document.querySelectorAll('.student-row').forEach(row => row.classList.remove('active'));
    const activeRow = document.querySelector('.student-row[data-id="' + data.id + '"]');
    if (activeRow) {
        activeRow.classList.add('active');
        activeRow.classList.add('played');
    }

    // Play audio
    if (playData.audio_url) {
        audioPlayer.src = playData.audio_url;
        audioPlayer.play();
    }
});

syncBtn.addEventListener('click', async () => {
    syncBtn.disabled = true;
    syncBtn.textContent = 'Syncing...';
    try {
        const resp = await fetch('/admin/api/sync', {method: 'POST'});
        const data = await resp.json();
        if (data.status === 'ok') {
            syncBtn.textContent = data.synced > 0 ? `Synced ${data.synced} new` : 'No new responses';
            if (data.synced > 0 && currentSessionId) {
                await loadStudents();
            }
        } else {
            syncBtn.textContent = 'Sync failed';
            alert('Sync error: ' + (data.message || 'Unknown error'));
        }
    } catch (e) {
        syncBtn.textContent = 'Sync error';
    }
    setTimeout(() => {
        syncBtn.textContent = 'Sync Forms';
        syncBtn.disabled = false;
    }, 2000);
});

resetBtn.addEventListener('click', async () => {
    if (!currentSessionId) return;
    if (!confirm('Reset all played states for this session?')) return;

    await fetch('/admin/api/ceremony/reset?session_id=' + currentSessionId, {method: 'POST'});
    await loadStudents();
    nowPlaying.style.display = 'none';
    playNextBtn.disabled = false;
});

init();
