let currentSessionId = null;
let students = [];
let pollTimer = null;
let toastTimer = null;
let busy = false;
let loadSeq = 0;
let checkinOpen = false;
let qrSessionId = null;

const HONORS_LABELS = {honors: 'Honors', highest_honors: 'Highest honors'};

const sessionSelect = document.getElementById('session-select');
const summary = document.getElementById('checkin-summary');
const emptyState = document.getElementById('checkin-empty');
const body = document.getElementById('checkin-body');
const tapPanel = document.getElementById('tab-tap');
const linePanel = document.getElementById('tab-line');
const tabButtons = document.querySelectorAll('.checkin-tab');
const switchBox = document.querySelector('.checkin-switch');
const switchLabel = document.getElementById('checkin-switch-label');
const switchBtn = document.getElementById('checkin-switch-btn');
const lineQr = document.getElementById('line-qr');
const lineUrl = document.getElementById('line-url');
const searchInput = document.getElementById('checkin-search');
const waitingList = document.getElementById('waiting-list');
const seatedList = document.getElementById('seated-list');
const clearBtn = document.getElementById('clear-btn');
const toast = document.getElementById('checkin-toast');

function showToast(text, isError) {
    clearTimeout(toastTimer);
    toast.textContent = text;
    toast.className = 'checkin-toast' + (isError ? ' error' : '');
    toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, isError ? 5000 : 2500);
}

async function init() {
    const resp = await fetch('/admin/api/events');
    const events = await resp.json();
    while (sessionSelect.options.length > 1) sessionSelect.remove(1);

    for (const event of events) {
        for (const session of event.sessions) {
            if (session.order !== 'seating') continue;
            const opt = document.createElement('option');
            opt.value = session.id;
            opt.textContent = event.name + ' — ' + session.label;
            sessionSelect.appendChild(opt);
        }
    }

    // Ushers should not have to pick when there is only one choice
    if (sessionSelect.options.length === 2) {
        sessionSelect.selectedIndex = 1;
        await selectSession();
    }
}

async function selectSession() {
    currentSessionId = sessionSelect.value;
    clearTimeout(pollTimer);
    students = [];
    qrSessionId = null;
    searchInput.value = '';
    if (!currentSessionId) {
        body.hidden = true;
        emptyState.hidden = false;
        summary.textContent = '';
        return;
    }
    await loadState();
    if (!linePanel.hidden) showLineQr();
}

async function loadState() {
    clearTimeout(pollTimer);
    const sessionId = currentSessionId;
    if (!sessionId) return;
    // A poll that started before a seat change must not overwrite the newer list
    const seq = ++loadSeq;
    try {
        const resp = await fetch(`/admin/api/sessions/${sessionId}/seating`);
        if (sessionId !== currentSessionId || seq !== loadSeq) return;
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            showToast(err.detail || 'Could not load the seating', true);
        } else {
            const data = await resp.json();
            students = data.students;
            checkinOpen = data.checkin_open;
            render();
        }
    } catch (e) {
        showToast('Connection problem, retrying', true);
    }
    // Other ushers and student check-in change the list, so keep it fresh
    pollTimer = setTimeout(() => { if (!busy) loadState(); }, 3000);
}

function lastName(name) {
    const parts = (name || '').trim().split(/\s+/);
    return parts[parts.length - 1].toLowerCase();
}

function makeMeta(s) {
    return [s.major, s.honors_level ? HONORS_LABELS[s.honors_level] : null].filter(Boolean).join(' · ');
}

function makeRow(s, seated) {
    const row = document.createElement('div');
    row.className = 'checkin-row' + (s.played ? ' announced' : '');

    if (seated) {
        const num = document.createElement('span');
        num.className = 'seat-number';
        num.textContent = String(s.seat_position);
        row.appendChild(num);
    }

    const who = document.createElement('div');
    who.className = 'who';
    const name = document.createElement('div');
    name.className = 'who-name';
    name.textContent = s.typed_name;
    const meta = document.createElement('div');
    meta.className = 'who-meta';
    meta.textContent = makeMeta(s);
    who.appendChild(name);
    who.appendChild(meta);
    row.appendChild(who);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = seated ? 'btn' : 'btn btn-primary';
    btn.textContent = seated ? 'Remove' : 'Seat';
    if (seated) {
        btn.disabled = s.played;
        btn.title = s.played ? 'Already announced' : '';
        btn.addEventListener('click', () => removeSeat(s, btn));
    } else {
        btn.addEventListener('click', () => seat(s, btn));
    }
    row.appendChild(btn);
    return row;
}

function render() {
    const seated = students.filter(s => s.seat_position !== null);
    const waiting = students
        .filter(s => s.seat_position === null)
        .sort((a, b) => lastName(a.typed_name).localeCompare(lastName(b.typed_name)) || a.typed_name.localeCompare(b.typed_name));

    emptyState.hidden = true;
    body.hidden = false;
    summary.textContent = `${seated.length} seated, ${waiting.length} waiting`;

    const query = searchInput.value.trim().toLowerCase();
    const shown = query
        ? waiting.filter(s => (s.typed_name + ' ' + (s.major || '')).toLowerCase().includes(query))
        : waiting;

    waitingList.replaceChildren();
    if (students.length === 0) {
        waitingList.appendChild(emptyMessage('No roster yet. Import one from the dashboard.'));
    } else if (waiting.length === 0) {
        waitingList.appendChild(emptyMessage('Everyone is seated.'));
    } else if (shown.length === 0) {
        waitingList.appendChild(emptyMessage('No one matches that search.'));
    }
    for (const s of shown) waitingList.appendChild(makeRow(s, false));

    seatedList.replaceChildren();
    if (seated.length === 0) seatedList.appendChild(emptyMessage('No one is seated yet.'));
    for (const s of seated) seatedList.appendChild(makeRow(s, true));

    clearBtn.disabled = seated.length === 0;
    renderSwitch();
}

function emptyMessage(text) {
    const div = document.createElement('div');
    div.className = 'checkin-empty-list';
    div.textContent = text;
    return div;
}

async function seat(s, btn) {
    if (busy) return;
    busy = true;
    btn.disabled = true;
    try {
        const resp = await fetch(`/admin/api/sessions/${currentSessionId}/seating/${s.id}`, {method: 'POST'});
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.detail || 'Could not seat this student', true);
        } else if (data.newly_seated) {
            showToast(`${s.typed_name} is seat ${data.seat_position}`);
        } else {
            showToast(`${s.typed_name} was already seated at ${data.seat_position}`);
        }
    } catch (e) {
        showToast('Connection problem, try again', true);
    }
    busy = false;
    await loadState();
}

async function removeSeat(s, btn) {
    if (busy) return;
    busy = true;
    btn.disabled = true;
    try {
        const resp = await fetch(`/admin/api/sessions/${currentSessionId}/seating/${s.id}`, {method: 'DELETE'});
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            showToast(err.detail || 'Could not remove this seat', true);
        } else {
            showToast(`Removed ${s.typed_name}`);
        }
    } catch (e) {
        showToast('Connection problem, try again', true);
    }
    busy = false;
    await loadState();
}

clearBtn.addEventListener('click', async () => {
    if (!currentSessionId || busy) return;
    if (!confirm('Remove every seat and reset played state for this session?')) return;
    busy = true;
    try {
        const resp = await fetch(`/admin/api/sessions/${currentSessionId}/seating`, {method: 'DELETE'});
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            showToast(err.detail || 'Could not clear the seating', true);
        } else {
            showToast('Seating cleared');
        }
    } catch (e) {
        showToast('Connection problem, try again', true);
    }
    busy = false;
    await loadState();
});

function renderSwitch() {
    switchBox.classList.toggle('open', checkinOpen);
    switchLabel.textContent = checkinOpen
        ? 'Student check-in is open. Students can scan now.'
        : 'Student check-in is closed. Scans are refused.';
    switchBtn.textContent = checkinOpen ? 'Close check-in' : 'Open check-in';
}

switchBtn.addEventListener('click', async () => {
    if (!currentSessionId || busy) return;
    busy = true;
    switchBtn.disabled = true;
    try {
        const resp = await fetch(`/admin/api/sessions/${currentSessionId}/checkin`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({open: !checkinOpen}),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            showToast(err.detail || 'Could not change check-in', true);
        }
    } catch (e) {
        showToast('Connection problem, try again', true);
    }
    busy = false;
    switchBtn.disabled = false;
    await loadState();
});

// The QR is drawn in the browser from a signed link the server made for this session
async function showLineQr() {
    const sessionId = currentSessionId;
    if (!sessionId || qrSessionId === sessionId) return;
    const resp = await fetch(`/admin/api/sessions/${sessionId}/checkin/links`);
    if (!resp.ok || sessionId !== currentSessionId) return;
    const {line_url} = await resp.json();

    const qr = qrcode(0, 'M');
    qr.addData(line_url);
    qr.make();
    const svg = new DOMParser().parseFromString(qr.createSvgTag({scalable: true, margin: 2}), 'image/svg+xml');
    lineQr.replaceChildren(svg.documentElement);
    lineUrl.textContent = line_url;
    lineUrl.href = line_url;
    qrSessionId = sessionId;
}

for (const tab of tabButtons) {
    tab.addEventListener('click', () => {
        for (const t of tabButtons) t.classList.toggle('active', t === tab);
        const line = tab.dataset.tab === 'line';
        tapPanel.hidden = line;
        linePanel.hidden = !line;
        if (line) showLineQr();
    });
}

searchInput.addEventListener('input', render);
sessionSelect.addEventListener('change', selectSession);

init();
